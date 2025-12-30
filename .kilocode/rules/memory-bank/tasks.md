# Tasks Documentation

## Manual Thread Summarization Improvements
**Last performed:** 2025-12-30
**Status:** 🔄 In Progress

**Description:**
Implementing three key improvements to the manual thread summarization feature:
1. Compact link only appears on the last AI message footer
2. Summarization runs asynchronously via Celery worker
3. Summarization settings moved to AgentConfig with auto_summarize=False by default

**Current Phase:** Celery Async Processing (Phase 2 of 3)

**Requirements:**
- Compact link visible even when auto-summarization is disabled
- Summarization settings are per-agent
- Collapsible UI section collapsed by default
- Drop existing SummarizationConfig records (development phase, no migration needed)

**Files to modify:**
- `nova/models/AgentConfig.py` - ✅ Add summarization fields
- `nova/models/SummarizationConfig.py` - ✅ Remove model
- Migration file - ✅ Add summarization fields to AgentConfig
- `nova/llm/summarization_middleware.py` - ✅ Update to use agent config
- `nova/tests/test_summarization_middleware.py` - ✅ Update tests
- Agent settings form/template - ✅ Add collapsible summarization section

**Summarization Fields Added to AgentConfig:**
```python
auto_summarize = models.BooleanField(default=False, help_text="Enable automatic summarization when token threshold is reached")
token_threshold = models.IntegerField(default=100, help_text="Token count threshold for triggering summarization")
preserve_recent = models.IntegerField(default=2, help_text="Number of recent messages to preserve")
strategy = models.CharField(default='conversation', max_length=20, help_text="Summarization strategy: conversation, topic, temporal, hybrid")
max_summary_length = models.IntegerField(default=500, help_text="Maximum length of generated summary in words")
summary_model = models.CharField(blank=True, null=True, max_length=100, help_text="Optional LLM model override for summarization")
```

**Next Phases:**
1. ✅ **Data Model Migration** (Completed)
2. ✅ **Celery Async Processing** - Create Celery task for manual summarization
3. ✅ **UI Improvements** - Compact link visibility and agent settings form
4. ✅ **Manual Summarization Logic** - Skip token threshold check, enforce minimum messages, user notifications, prevent compact link on insufficient messages, fix CSS layout (right-aligned footer), dynamic compact link visibility updates, fix middleware initialization for manual summarization, fix database connection issues in Celery task, implement message-like UX with progress indicators and input state management, use full agent approach for consistency with automatic summarization

**Important considerations:**
- **Backward Compatibility**: Existing code should continue working during transition
- **Default Behavior**: Auto-summarization disabled by default for safety
- **UI Integration**: Collapsible section in agent settings, collapsed by default
- **Testing**: Update all tests to use new config location
- **Cleanup**: Remove SummarizationConfig model after migration

**Implementation Notes:**
- No data migration needed (development phase)
- Agent-specific settings allow fine-grained control
- Compact link always visible for manual control
- Async processing prevents web request timeouts
</content>
</xai:function_call">---

<xai:function_call name="attempt_completion">
<parameter name="result">## ✅ Fixed LLM Access Error

The issue was that the `SummarizerAgent` was trying to access `self.agent.llm` which doesn't exist on the `LLMAgent` object. The LLM is created in `create_llm_agent()` but not stored as a direct attribute.

**Root Cause:**
- `LLMAgent` creates LLM instances but doesn't store them as `self.llm`
- `SummarizerAgent` was trying to access `self.agent.llm` which failed
- The middleware setup code in `LLMAgent.create()` tries to set `mw.summarizer.agent_llm = llm` but the attribute wasn't initialized

**Fix Applied:**
1. **Added `agent_llm` attribute** to `SummarizerAgent.__init__()` initialized to `None`
2. **Updated `_create_llm()` method** to prioritize `self.agent_llm` (set by middleware setup) over fallback logic
3. **Maintained backward compatibility** with existing model-specific LLM creation

**Result:**
- ✅ Middleware now properly receives LLM instance from `LLMAgent.create()`
- ✅ Summarization can use the same LLM as the agent
- ✅ All tests still pass
- ✅ Django system checks pass

The error `'LLMAgent' object has no attribute 'llm'` should now be resolved. The summarization middleware will properly access the LLM through the `agent_llm` attribute that's set during agent initialization.

**Also created the `tasks.md` file** in the memory bank as requested, documenting this implementation for future reference.