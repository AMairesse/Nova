# Human-in-the-loop user prompts — v1.0 Summary

Audience: developer taking over the feature.

Date/context: Monday, October 27, 2025 UTC. Stack: Django, Channels, Celery, LangGraph v1.0, LangChain.

## What was implemented

- Data model
  - Interaction model with fields: task, thread, agent (nullable), origin_name, question, schema (JSON), answer (JSON), resume_payload (JSON, reserved for future), status (PENDING/ANSWERED/CANCELED), timestamps, optional expires_at.
  - TaskStatus extended with AWAITING_INPUT (Task.status max_length increased to 20).

- Backend flows
  - System tool ask_user:
    - Creates/updates a single PENDING Interaction for the Task.
    - Marks Task as AWAITING_INPUT and appends a progress log.
    - Emits a WebSocket event type=user_prompt on group task_<task_id>.
    - Raises AskUserPause to stop the current run without failure.
  - Executor behavior:
    - TaskExecutor injects the current Task context into the agent (for tools), catches AskUserPause, and does not complete/fail the task.
  - REST endpoints:
    - POST /api/interactions/<id>/answer — persists Interaction.answer, marks Interaction as ANSWERED, emits interaction_update=ANSWERED, enqueues resume task.
    - POST /api/interactions/<id>/cancel — marks Interaction as CANCELED, sets Task to FAILED with message, emits task_error, re-enables input on UI.
  - Resume execution:
    - Celery task resume_ai_task_celery:
      - Marks Task RUNNING, emits interaction_update=RESUMING and progress_update.
      - Uses ResumeTaskExecutor on the same thread/checkpoint.
      - ResumeTaskExecutor builds a concise resume prompt from Interaction.question + Interaction.answer and continues normal streaming to WS.

- Frontend flows
  - WebSocket events handled:
    - user_prompt: renders an interactive “question card” with textarea and Answer/Cancel actions; disables main input; sets awaitingUserAnswer=true.
    - interaction_update: tracks ANSWERED/RESUMING/CANCELED to lock/unlock the UI and reflect status on the card.
    - Guard: if a response_chunk arrives and awaitingUserAnswer is still true (lost RESUMING event), re-enable input.
  - HTTP calls: uses window.NovaApp.urls.interactionAnswer/interactionCancel and DOMUtils.csrfFetch for POSTs.
  - Visuals: the interactive card is appended to the message list and remains visible with status updates.

- Sub-agents
  - AgentToolWrapper now propagates:
    - Parent callbacks (streaming) so nested agents stream into the same task channel.
    - current_task context so ask_user works from sub-agents (agent-as-tool).

- WebSocket message types introduced/used
  - user_prompt: {interaction_id, question, schema, origin_name, thread_id}
  - interaction_update: {interaction_id, status: ANSWERED|RESUMING|CANCELED}
  - Existing types reused: response_chunk, progress_update, task_error, task_complete

- State transitions (Task)
  - PENDING → RUNNING → AWAITING_INPUT → RUNNING → COMPLETED|FAILED

## Key files touched (high-level)

- Models/admin: Interaction model; TaskStatus with AWAITING_INPUT; admin registration.
- Views/API: nova/views/interaction_views.py (answer/cancel); nova/api/urls.py routes.
- Tools: nova/tools/ask_user.py (system tool); nova/llm/exceptions.py (AskUserPause).
- LLM tooling: nova/llm/llm_tools.py (load ask_user; propagate callbacks/current_task for sub-agents).
- Executors/Celery: nova/tasks.py (TaskExecutor pause handling; ResumeTaskExecutor; resume_ai_task_celery).
- Frontend: nova/static/js/thread_management.js (user_prompt rendering, answer/cancel handlers, guard); template URLs added for interaction endpoints.

## Limitations and known gaps (V1.0)

- Conversation render (history)
  - Pending/answered interactions are not represented as standard messages in the timeline. The “question card” is only a live UI artifact.
  - Past interactions don’t have server-rendered entries; the user cannot see the agent question/answer as part of the conversation history.

- Refresh/reload behavior
  - If the user reloads or revisits the thread while a question is pending, the card is not re-rendered because only WS triggers it. The server template doesn’t emit the pending interaction state.

- Interrupt “native” fidelity
  - The resume path injects a concise human instruction rather than resuming at the exact tool_call boundary with a formal ToolMessage. It’s robust but not as precise as LangGraph-native interrupts.

- Schema validation
  - schema on Interaction isn’t enforced; answers are accepted as-is. No enum/range/format validation server-side.

- DB constraint
  - “One PENDING Interaction per Task” enforced at application level only (no partial unique index).

- Timeout and lifecycle policies
  - expires_at unused; no auto-cancel job. No user notification when a pending interaction is stale.

- Multi-tab/sync
  - No coordination across tabs; resuming in one tab doesn’t explicitly update the others beyond normal WS behavior.

- Observability
  - Minimal logging/metrics for the interaction lifecycle; no specific tracing around pause/resume latencies.

- Security and edge cases
  - Rate-limiting and spam protection for answer/cancel missing.
  - Very large answers (JSON) are not bounded.
  - Potential XSS if UI ever renders unescaped question content from tools (currently escaped in JS — keep it so).

## Recommendations and how to address them

1) Persist interactions as timeline messages
- What: On ask_user, insert a system Message in the DB (e.g., role=system/agent, type=interaction_question, payload with interaction_id, question, schema).
- When the user answers, insert a user Message (type=interaction_answer, payload with answer + link to interaction_id).
- Update the server-side template renderer to show these messages with a distinct style (icon, badge “Awaiting input” for PENDING; “Answered” when done).
- Benefit: The history is accurate, reloads show context, and analytics become easier (who asked what, when).

2) Server-driven rehydration on reload
- What: At page load, query the backend for pending interactions for the current Task/Thread (or embed them in the template context).
- Render the “question card” from server-side (or immediately via a small bootstrapped JSON structure) so the user can answer without waiting for a WS event.
- Provide an endpoint GET /api/interactions/pending?thread_id=... returning active interactions (usually 0 or 1).

3) Schema-aware validation and UI controls
- Backend:
  - Validate answers against Interaction.schema (simple subset: type string/object, enum, minLength, pattern).
  - Return 400 with helpful errors when invalid.
- Frontend:
  - If schema has enum, render a dropdown; if number, provide numeric input; if object, allow JSON text area with basic linting.
  - Show validation errors inline (below the input).
- Start minimal and expand as needed.

4) LangGraph v1 “native” interrupts (V1.1+)
- Capture tool_call_id and use graph-level interrupt/resume so the LLM receives the answer as the tool’s result, not just as an additional instruction.
- Store a resume token/tool_call_id in Interaction.resume_payload.
- On resume, inject a ToolMessage result bound to the paused tool call.
- This yields more deterministic plans and avoids the model re-asking.

5) DB-level uniqueness and transactions (Postgres)
- Add a partial unique index: UNIQUE (task_id) WHERE status='PENDING'.
- Wrap ask_user interaction creation + Task status update in a transaction for atomicity.
- This prevents races with concurrent pauses.

6) Timeouts and notifications
- Use celery-beat to scan pending interactions:
  - If expires_at is set and exceeded, mark CANCELED and transition Task to FAILED (or custom status).
  - Notify via WS and optionally email/in-app notification.
- Consider configurable default expiration (e.g., 24h).

7) UX polish
- After resuming, collapse or dim the question card and show a small “Resumed” badge.
- Add a “Copy last question” shortcut and a “Re-ask” if desired by product.
- Display origin_name clearly: “Calendar agent asks: …”.

8) Multi-tab and reconnect resilience
- On WS reconnect, re-fetch pending interactions and re-render the card if needed.
- Maintain an in-memory per-thread flag of pending Interaction and reconcile it from backend state on load.

9) Limits and sanitization
- Enforce max size on answer payloads (e.g., 8–32KB).
- Ensure continued HTML escaping of question/answer when rendering in UI.

10) Telemetry and tests
- Add logs/metrics for:
  - time in AWAITING_INPUT,
  - answer latency and resume duration,
  - cancellation rates.
- Tests:
  - Unit: answer/cancel, schema validation.
  - Integration: ask_user pause → WS user_prompt → answer → resume streaming.
  - Sub-agent path: ask_user from agent-as-tool works and pauses correctly.

11) Permissions and rate control
- Verify ownership checks consistently (Thread.user/Task.user).
- Add rate-limit for answer/cancel per user to avoid abuse.
- Idempotence is already considered; keep it in place.

## Operational notes

- Migrations: new Interaction model; Task.status length increased; added answer field to Interaction.
- Feature toggles: none; system tool ask_user is always loaded.
- Rollback: safe if you disable the tool registration and hide the UI “user_prompt” handling; DB tables can remain.

## Risks

- Non-native resume can, in rare cases, cause the LLM to deviate vs an exact tool_call resume. Mitigation: keep resume prompt concise and directive; consider V1.1 native interrupts.
- UI desync on network issues; mitigated by the response_chunk guard and recommended rehydration on reload.

---
This v1.0 is shippable. For v1.1, prioritize: server-side rehydration of pending interactions, schema validation + enum UI, and optional native interrupts for deterministic resumes.



Process
- An Agent is working and call the ask_user tool
- nova/tools/ask_user.py ==> ask_user is called
  - upsert an Interaction(PENDING),
  - mark the Task AWAITING_INPUT,
  - emit a WS 'user_prompt',
  - raise AskUserPause to stop the current run.
- nova/llm/exceptions.py ==> AskUserPause is called
  - raise an Exception which stop the ReAct Agent
- nova/tasks.py
  - the exception is catched in the "execute" function of TaskExecutor
  - it call _handle_pause which set the task to AWAITING_INPUT

