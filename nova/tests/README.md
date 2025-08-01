# Nova Test Suite

This directory contains the comprehensive test suite for the Nova Django application. The tests are organized by functionality and follow Django testing best practices.

## Test Organization

### Base Classes (`base.py`)
- **BaseTestCase**: Common setup for all tests
- **BaseModelTestCase**: Base for model tests with user setup
- **BaseViewTestCase**: Base for view tests with authenticated client
- **BaseAPITestCase**: Base for API tests with JSON content type
- **BaseAgentTestCase**: Base for tests requiring Agent setup
- **BaseToolTestCase**: Base for tests requiring Tool setup
- **BaseIntegrationTestCase**: Base for integration tests with full setup

### Model Tests
- **`test_provider_model.py`**: LLMProvider model tests (18 tests)
  - Creation and validation
  - Provider type choices
  - API key encryption
  - JSON config handling
  - User relationships
  - Edge cases and constraints

- **`test_user_models.py`**: UserProfile and UserParameters tests (18 tests)
  - Automatic creation via signals
  - Encrypted field handling
  - One-to-one relationships
  - Default agent assignment
  - Langfuse configuration

- **`test_task_model.py`**: Task model tests (22 tests)
  - Status management and transitions
  - Progress logs handling
  - Relationships with Thread and Agent
  - JSON field handling
  - Cascade deletions

- **`test_thread_model.py`**: Thread and Message model tests (15 tests)
  - Message creation with different actors
  - Thread-message relationships
  - Message ordering and filtering
  - Helper method functionality

### View Tests
- **`test_views.py`**: Core view functionality
- **`test_agent_views.py`**: Agent CRUD operations
- **`test_provider_views.py`**: Provider management views
- **`test_ajax.py`**: AJAX endpoint tests
- **`test_api.py`**: REST API tests

### Form Tests
- **`test_forms.py`**: Form validation and processing

### Tool Tests
- **`test_tool_model.py`**: Tool model functionality
- **`test_tool_credential.py`**: Tool credential management
- **`test_caldav.py`**: CalDAV tool integration
- **`test_mcp_client.py`**: MCP client functionality

### Integration Tests
- **`test_llm_agent.py`**: LLM agent creation and tool loading
- **`test_run_ai_task.py`**: AI task execution
- **`test_urls.py`**: URL routing and access control

## Test Coverage

The test suite provides comprehensive coverage of:

### Models (100+ tests)
- ✅ All model fields and relationships
- ✅ Validation and constraints
- ✅ Encrypted field handling
- ✅ JSON field operations
- ✅ Cascade deletions
- ✅ Signal-based creation
- ✅ Edge cases and error handling

### Views (50+ tests)
- ✅ Authentication requirements
- ✅ CRUD operations
- ✅ Form processing
- ✅ AJAX endpoints
- ✅ API responses
- ✅ Error handling

### Forms (20+ tests)
- ✅ Field validation
- ✅ Custom clean methods
- ✅ Dynamic field behavior
- ✅ JSON schema validation

### Tools & Integration (40+ tests)
- ✅ Tool discovery and loading
- ✅ MCP client functionality
- ✅ CalDAV integration
- ✅ Agent tool relationships
- ✅ Task execution

## Running Tests

### Run All Tests
```bash
python manage.py test nova.tests
```

### Run Specific Test Categories
```bash
# Model tests only
python manage.py test nova.tests.test_*_model*

# View tests only
python manage.py test nova.tests.test_*_views nova.tests.test_views

# Tool tests only
python manage.py test nova.tests.test_tool* nova.tests.test_caldav nova.tests.test_mcp*
```

### Run Individual Test Files
```bash
python manage.py test nova.tests.test_provider_model
python manage.py test nova.tests.test_user_models
python manage.py test nova.tests.test_task_model
```

### Run with Verbose Output
```bash
python manage.py test nova.tests -v 2
```

## Test Quality Standards

### Model Tests
- Test all CRUD operations
- Verify field validation and constraints
- Test relationships and cascade behavior
- Cover edge cases and error conditions
- Test string representations
- Verify encrypted field functionality

### View Tests
- Test authentication requirements
- Verify proper HTTP status codes
- Test form processing and validation
- Check template context variables
- Test AJAX responses
- Verify redirect behavior

### Integration Tests
- Test complete user workflows
- Verify tool integration
- Test task execution pipelines
- Check error handling across components

## Recent Improvements

### Test Organization
- ✅ Created comprehensive base test classes
- ✅ Eliminated duplicate test code
- ✅ Organized tests by functionality
- ✅ Removed outdated test files

### Model Test Coverage
- ✅ Added comprehensive LLMProvider tests
- ✅ Added UserProfile/UserParameters tests
- ✅ Added Task model tests
- ✅ Updated Thread model tests

### Test Quality
- ✅ Fixed failing tests
- ✅ Improved test documentation
- ✅ Added edge case coverage
- ✅ Enhanced error condition testing

## Maintenance

### Adding New Tests
1. Inherit from appropriate base class
2. Follow naming conventions (`test_*`)
3. Add comprehensive docstrings
4. Test both success and failure cases
5. Update this README if adding new categories

### Test Data
- Use factories or fixtures for complex setup
- Clean up test data in tearDown if needed
- Use transactions for database tests
- Mock external dependencies

### Performance
- Keep tests fast and focused
- Use `setUpClass` for expensive setup
- Mock slow operations
- Avoid unnecessary database queries

## Current Status

**Total Tests**: 213  
**Status**: ✅ All Passing  
**Coverage**: Comprehensive across all major components  
**Last Updated**: January 2025
