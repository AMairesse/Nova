# Tasks Documentation

## Sub-Agent Confirmation for Manual Thread Summarization
**Last performed:** 2026-01-03
**Status:** 📋 Planned

**Description:**
Implementing user confirmation when manually summarizing threads that contain sub-agents with accumulated context. When a user clicks "Compact", the system checks if sub-agents have sufficient messages (> preserve_recent) and shows a confirmation dialog displaying token counts, allowing choice between summarizing only the main agent or all sub-agents.

**Requirements:**
- Check sub-agents with len(messages) > agent.preserve_recent for sufficient context
- Display token counts in confirmation dialog for user understanding
- Use each sub-agent's specific configuration settings
- Allow user to choose: main agent only vs. main + sub-agents
- Sequential processing of multiple agents with progress updates
- Proper error handling if sub-agent summarization fails

**Files to modify:**
- `nova/views/thread_views.py` - Modify summarize_thread view to check sub-agents and return CONFIRMATION_NEEDED
- `nova/urls.py` - Add URL for confirm_summarize_thread endpoint
- `nova/views/thread_views.py` - Add confirm_summarize_thread view
- `nova/tasks/tasks.py` - Update SummarizationTaskExecutor to handle multiple agents
- Frontend JavaScript - Add confirmation dialog handling
- Frontend HTML - Add confirmation dialog template

**Key Implementation Details:**
- **Logic Check**: `len(messages) > agent_config.preserve_recent` ensures sufficient context
- **UI Display**: Show token counts calculated via `agent.count_tokens(messages)`
- **Per-Agent Config**: Each sub-agent uses its own preserve_recent setting
- **Sequential Processing**: Summarize agents one by one to avoid resource conflicts
- **Progress Updates**: Each agent sends WebSocket progress updates
- **Error Handling**: Continue with other agents if one fails

**Important considerations:**
- **User Control**: Explicit choice prevents automatic sub-agent processing
- **Transparency**: Token counts help users understand context size
- **Performance**: Only processes agents with sufficient context
- **Safety**: Independent of auto-summarization settings
- **Backward Compatibility**: Existing single-agent summarization unchanged