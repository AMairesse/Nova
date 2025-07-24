# nova/tests/test_mcp_client.py
"""
Tests for MCP (Model Context Protocol) client functionality.
"""

from __future__ import annotations

import asyncio
from unittest.mock import Mock, patch, AsyncMock
from django.test import TestCase
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.http import Http404

from nova.models import Tool, ToolCredential
from nova.mcp.client import MCPClient


class MCPClientTestCase(TestCase):
    """Test cases for MCPClient functionality."""
    
    def setUp(self):
        """Set up test data."""
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        
        self.tool = Tool.objects.create(
            user=self.user,
            name='Test MCP Tool',
            description='Test MCP tool for unit testing',
            tool_type=Tool.ToolType.MCP,
            endpoint='https://example.com/mcp'
        )
        
        self.credential = ToolCredential.objects.create(
            user=self.user,
            tool=self.tool,
            auth_type='token',
            token='test-token-123'
        )
        
        # Clear cache before each test
        cache.clear()
    
    def test_client_initialization(self):
        """Test MCPClient initialization."""
        client = MCPClient(self.tool.endpoint, self.credential)
        
        # Client doesn't normalize endpoints
        self.assertEqual(client.endpoint, 'https://example.com/mcp')
        self.assertEqual(client.credential, self.credential)
    
    def test_endpoint_stored_as_is(self):
        """Test that endpoints are stored without modification."""
        # Without trailing slash
        client1 = MCPClient('https://example.com/mcp', self.credential)
        self.assertEqual(client1.endpoint, 'https://example.com/mcp')
        
        # With trailing slash
        client2 = MCPClient('https://example.com/mcp/', self.credential)
        self.assertEqual(client2.endpoint, 'https://example.com/mcp/')
    
    @patch('nova.mcp.client.BearerAuth')
    def test_auth_object_token(self, mock_bearer):
        """Test authentication object generation for token auth."""
        mock_bearer.return_value = Mock(token='test-token-123')
        
        client = MCPClient(self.tool.endpoint, self.credential)
        auth = client._auth_object()
        
        self.assertIsNotNone(auth)
        mock_bearer.assert_called_once_with('test-token-123')
    
    def test_auth_object_none(self):
        """Test authentication object generation for no auth."""
        self.credential.auth_type = 'none'
        self.credential.token = None
        self.credential.save()
        
        client = MCPClient(self.tool.endpoint, self.credential)
        auth = client._auth_object()
        
        self.assertIsNone(auth)
    
    def test_auth_object_no_credential(self):
        """Test authentication object generation without credentials."""
        client = MCPClient(self.tool.endpoint, None)
        auth = client._auth_object()
        
        self.assertIsNone(auth)
    
    @patch('nova.mcp.client.FastMCPClient')
    def test_list_tools_sync(self, mock_client_class):
        """Test synchronous tool listing."""
        # Mock the async context manager and list_tools
        mock_client = AsyncMock()
        # Create proper tool data structures instead of Mock objects
        mock_tools = [
            type('Tool', (), {
                'name': 'weather',
                'description': 'Get weather',
                'input_schema': {'type': 'object'}
            })(),
            type('Tool', (), {
                'name': 'stocks',
                'description': 'Get stocks',
                'input_schema': {'type': 'object'}
            })()
        ]
        mock_client.list_tools = AsyncMock(return_value=mock_tools)
        
        mock_client_class.return_value.__aenter__.return_value = mock_client
        
        client = MCPClient(self.tool.endpoint, self.credential)
        tools = client.list_tools(user_id=self.user.id)
        
        self.assertEqual(len(tools), 2)
        self.assertEqual(tools[0]['name'], 'weather')
        self.assertEqual(tools[0]['description'], 'Get weather')
        self.assertEqual(tools[1]['name'], 'stocks')
        self.assertEqual(tools[1]['description'], 'Get stocks')
    
    @patch('nova.mcp.client.FastMCPClient')
    def test_list_tools_cached(self, mock_client_class):
        """Test that tool listing uses cache."""
        # First call - should hit the API
        mock_client = AsyncMock()
        # Create serializable tool data
        mock_tools = [
            type('Tool', (), {
                'name': 'weather',
                'description': 'Get weather',
                'input_schema': {'type': 'object'}
            })()
        ]
        mock_client.list_tools = AsyncMock(return_value=mock_tools)
        mock_client_class.return_value.__aenter__.return_value = mock_client
        
        client = MCPClient(self.tool.endpoint, self.credential)
        tools1 = client.list_tools(user_id=self.user.id)
        
        # Second call - should use cache
        tools2 = client.list_tools(user_id=self.user.id)
        
        self.assertEqual(tools1, tools2)
        # The mock should only be called once due to caching
        mock_client.list_tools.assert_awaited_once()
    
    @patch('nova.mcp.client.FastMCPClient')
    def test_list_tools_force_refresh(self, mock_client_class):
        """Test force refresh bypasses cache."""
        # Define async __aenter__ and __aexit__ functions
        async def aenter1(*args):
            return mock_client1
        
        async def aexit1(*args):
            return None
        
        async def aenter2(*args):
            return mock_client2
        
        async def aexit2(*args):
            return None
        
        mock_client1 = AsyncMock()
        mock_tools_objects1 = [
            type('Tool', (), {
                'name': 'weather',
                'description': 'Get weather',
                'input_schema': {'type': 'object'},
                'output_schema': {}
            })()
        ]
        mock_client1.list_tools = AsyncMock(return_value=mock_tools_objects1)
        
        mock_client2 = AsyncMock()
        mock_tools_objects2 = [
            type('Tool', (), {
                'name': 'stocks',
                'description': 'Get stocks',
                'input_schema': {'type': 'object'},
                'output_schema': {}
            })()
        ]
        mock_client2.list_tools = AsyncMock(return_value=mock_tools_objects2)
        
        # Side effect for two instances
        mock_client_class.side_effect = [
            type('Ctx', (), {'__aenter__': aenter1, '__aexit__': aexit1})(),
            type('Ctx', (), {'__aenter__': aenter2, '__aexit__': aexit2})()
        ]
        
        client = MCPClient(self.tool.endpoint, self.credential)
        
        # First call (caches result)
        tools1 = client.list_tools(user_id=self.user.id)
        
        # Expected converted dicts for assertion
        expected_tools1 = [
            {
                'name': 'weather',
                'description': 'Get weather',
                'input_schema': {'type': 'object'},
                'output_schema': {}
            }
        ]
        self.assertEqual(tools1, expected_tools1)
        
        # Second call with force_refresh (should fetch again)
        tools2 = client.list_tools(user_id=self.user.id, force_refresh=True)
        
        expected_tools2 = [
            {
                'name': 'stocks',
                'description': 'Get stocks',
                'input_schema': {'type': 'object'},
                'output_schema': {}
            }
        ]
        self.assertEqual(tools2, expected_tools2)
        
        # Called twice
        self.assertEqual(mock_client_class.call_count, 2)
        mock_client1.list_tools.assert_awaited_once()
        mock_client2.list_tools.assert_awaited_once()
    
    @patch('nova.mcp.client.FastMCPClient')
    def test_call_sync(self, mock_client_class):
        """Test synchronous tool call."""
        mock_client = AsyncMock()
        mock_client.call_tool = AsyncMock(return_value={'result': 'success'})
        mock_client_class.return_value.__aenter__.return_value = mock_client
        
        client = MCPClient(self.tool.endpoint, self.credential)
        result = client.call('test_tool', param1='value1', param2='value2')
        
        self.assertEqual(result, {'result': 'success'})
        mock_client.call_tool.assert_awaited_once_with(
            'test_tool', param1='value1', param2='value2'
        )
    
    @patch('nova.mcp.client.FastMCPClient')
    def test_call_validates_inputs(self, mock_client_class):
        """Test that call validates input types."""
        client = MCPClient(self.tool.endpoint, self.credential)
        
        mock_client = AsyncMock()
        mock_client.call_tool = AsyncMock(return_value={'result': 'success'})
        mock_client_class.return_value.__aenter__.return_value = mock_client
        
        # Invalid type (nested invalid leaf: object() non autorisé)
        with self.assertRaises(ValidationError) as ctx:
            client.call('test_tool', data={'nested': object()})
        self.assertIn('Unsupported type', str(ctx.exception))
        
        # String too long
        with self.assertRaises(ValidationError) as ctx:
            client.call('test_tool', text='x' * 3000)
        self.assertIn('too long', str(ctx.exception))
    
    @patch('nova.mcp.client.FastMCPClient')
    @patch('nova.mcp.client.logger.error')
    def test_call_handles_404(self, mock_logger_error, mock_client_class):
        """Test that 404 errors are converted to Django Http404."""
        mock_client = AsyncMock()
        
        # Create a mock httpx response
        mock_response = Mock()
        mock_response.status_code = 404
        
        # Import httpx properly for the exception
        import httpx
        error = httpx.HTTPStatusError(
            "Not found", request=Mock(), response=mock_response
        )
        mock_client.call_tool = AsyncMock(side_effect=error)
        mock_client_class.return_value.__aenter__.return_value = mock_client
        
        client = MCPClient(self.tool.endpoint, self.credential)
        
        with self.assertRaises(Http404):
            client.call('nonexistent_tool')
    
    @patch('nova.mcp.client.FastMCPClient')
    @patch('nova.mcp.client.logger.error')
    def test_call_handles_connection_error(self, mock_logger_error, mock_client_class):
        """Test that connection errors are properly handled."""
        mock_client = AsyncMock()
        
        import httpx
        error = httpx.RequestError("Connection failed")
        mock_client.call_tool = AsyncMock(side_effect=error)
        mock_client_class.return_value.__aenter__.return_value = mock_client
        
        client = MCPClient(self.tool.endpoint, self.credential)
        
        with self.assertRaises(ConnectionError) as ctx:
            client.call('test_tool')
        self.assertIn('MCP server unreachable', str(ctx.exception))
    
    def test_async_context_check(self):
        """Test that sync methods raise error when called from async context."""
        client = MCPClient(self.tool.endpoint, self.credential)
        
        async def async_caller():
            # This should raise RuntimeError
            client.list_tools()
        
        with self.assertRaises(RuntimeError) as ctx:
            asyncio.run(async_caller())
        self.assertIn('Blocking method called from async context', str(ctx.exception))

    def test_async_context_check(self):
        """Test that sync methods raise error when called from async context."""
        client = MCPClient(self.tool.endpoint, self.credential)
        
        async def async_caller():
            # This should raise RuntimeError
            client.list_tools()
        
        with self.assertRaises(RuntimeError) as ctx:
            asyncio.run(async_caller())
        self.assertIn('Blocking method called from async context', str(ctx.exception))

class MCPIntegrationTestCase(TestCase):
    """Integration tests for MCP functionality with agents."""
    
    def setUp(self):
        """Set up test data."""
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        
        # Clear cache
        cache.clear()
    
    @patch('nova.mcp.client.MCPClient.list_tools')
    def test_mcp_tools_discovery_in_views(self, mock_list_tools):
        """Test MCP tool discovery in the test connection view."""
        from nova.models import LLMProvider
        
        # Create test data
        tool = Tool.objects.create(
            user=self.user,
            name='Test MCP',
            description='Test MCP tool',
            tool_type=Tool.ToolType.MCP,
            endpoint='https://example.com/mcp/',
            is_active=True
        )
        
        ToolCredential.objects.create(
            user=self.user,
            tool=tool,
            auth_type='token',
            token='test-token'
        )
        
        # Mock tool discovery
        mock_list_tools.return_value = [
            {
                'name': 'weather_forecast',
                'description': 'Get weather forecast',
                'input_schema': {
                    'type': 'object',
                    'properties': {
                        'city': {'type': 'string'}
                    }
                }
            }
        ]
        
        # Login and test the connection
        self.client.login(username='testuser', password='testpass123')
        response = self.client.post(f'/tool/test-connection/{tool.id}/')
        
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'success')
        self.assertIn('tools', data)
        self.assertEqual(len(data['tools']), 1)
        self.assertEqual(data['tools'][0]['name'], 'weather_forecast')


class MCPCacheTests(TestCase):
    """Tests for caching in MCPClient."""

    def setUp(self):
        """Set up test data."""
        self.user = User.objects.create_user(
            username='testuser',
            password='testpass123'
        )

        self.tool = Tool.objects.create(
            user=self.user,
            name='Test MCP Tool',
            tool_type=Tool.ToolType.MCP,
            endpoint='https://example.com/mcp'
        )

        self.credential = ToolCredential.objects.create(
            user=self.user,
            tool=self.tool,
            auth_type='token',
            token='test-token-123'
        )

        # Clear cache before each test
        cache.clear()

    @patch('nova.mcp.client.FastMCPClient')
    def test_acall_caching_same_args(self, mock_client_class):
        """Test that acall caches results for same arguments (cache hit after first call)."""
        client = MCPClient(self.tool.endpoint, self.credential)

        # Mock FastMCPClient
        mock_fast_client = AsyncMock()
        mock_fast_client.call_tool = AsyncMock(return_value={'result': 'cached_value'})
        mock_client_class.return_value.__aenter__.return_value = mock_fast_client
        mock_client_class.return_value.__aexit__ = AsyncMock(return_value=None)

        async def run_call():
            return await client.acall('test_tool', param='value')

        # First call - should hit the network
        result1 = asyncio.run(run_call())

        # Second call with same args - should hit cache
        result2 = asyncio.run(run_call())

        self.assertEqual(result1, {'result': 'cached_value'})
        self.assertEqual(result2, {'result': 'cached_value'})
        # call_tool called only once
        self.assertEqual(mock_fast_client.call_tool.await_count, 1)

    @patch('nova.mcp.client.FastMCPClient')
    def test_acall_no_cache_different_args(self, mock_client_class):
        """Test that different args result in cache miss (network called twice)."""
        client = MCPClient(self.tool.endpoint, self.credential)

        # Mock FastMCPClient
        mock_fast_client = AsyncMock()
        mock_fast_client.call_tool.side_effect = [
            {'result': 'value1'},
            {'result': 'value2'}
        ]
        mock_client_class.return_value.__aenter__.return_value = mock_fast_client
        mock_client_class.return_value.__aexit__ = AsyncMock(return_value=None)

        async def run_call(param):
            return await client.acall('test_tool', param=param)

        # First call
        result1 = asyncio.run(run_call('value1'))

        # Second call with different args
        result2 = asyncio.run(run_call('value2'))

        self.assertEqual(result1, {'result': 'value1'})
        self.assertEqual(result2, {'result': 'value2'})
        # call_tool called twice (different args → cache miss)
        self.assertEqual(mock_fast_client.call_tool.await_count, 2)
