# nova/views/tools_views.py
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, reverse, get_object_or_404
from django.http import JsonResponse
from django.utils.translation import gettext_lazy as _, ngettext
from django.views.decorators.http import require_POST
from nova.models.models import Tool, ToolCredential
from nova.forms import ToolForm, ToolCredentialForm
from nova.tools import get_metadata, get_tool_type
from nova.mcp.client import MCPClient
from asgiref.sync import sync_to_async
import logging

logger = logging.getLogger(__name__)


@login_required
@require_POST
def create_tool(request):
    if request.method == 'POST':
        form = ToolForm(request.POST)
        if form.is_valid():
            # Create the tool
            tool = form.save(commit=False)
            tool.user = request.user
            tool.save()

            # Automatically create a ToolCredential for built-in tools
            if tool.tool_type == Tool.ToolType.BUILTIN:
                tool_metadata = get_tool_type(tool.tool_subtype)
                if tool_metadata:
                    ToolCredential.objects.get_or_create(
                        user=request.user,
                        tool=tool,
                        defaults={
                            'auth_type': tool_metadata.get('auth_type',
                                                           'basic')
                        }
                    )

            return redirect(reverse('user_config') + '?tab=tools')
        else:
            # Store errors in the session
            request.session['tool_errors'] = form.errors.as_json()
            return redirect(reverse('user_config') + '?tab=tools&error=1')

    return redirect(reverse('user_config') + '?tab=tools')


@login_required
@require_POST
def edit_tool(request, tool_id):
    tool = get_object_or_404(Tool, id=tool_id, user=request.user)

    if request.method == 'POST':
        form = ToolForm(request.POST, instance=tool)
        if form.is_valid():
            form.save()
            return redirect(reverse('user_config') + '?tab=tools')
        else:
            request.session['tool_errors'] = form.errors.as_json()
            return redirect(reverse('user_config') + '?tab=tools&error=1')

    return redirect(reverse('user_config') + '?tab=tools')


@login_required
@require_POST
def delete_tool(request, tool_id):
    tool = get_object_or_404(Tool, id=tool_id, user=request.user)

    if request.method == 'POST':
        if tool.agents.exists():
            tool.agents.clear()

        ToolCredential.objects.filter(tool=tool).delete()

        tool.delete()

    return redirect(reverse('user_config') + '?tab=tools')


@login_required
@require_POST
def configure_tool(request, tool_id):
    tool = get_object_or_404(Tool, id=tool_id, user=request.user)

    if request.method == 'POST':
        tool_credential, created = ToolCredential.objects.get_or_create(
            user=request.user,
            tool=tool,
            defaults={'auth_type': 'basic'}
        )

        if tool.tool_subtype == "caldav":
            tool_credential.config = {
                'caldav_url': request.POST.get('caldav_url', ''),
                'username': request.POST.get('username', ''),
                'password': request.POST.get('password', '') or tool_credential.config.get('password', '')
            }
            tool_credential.save()
            return redirect(reverse('user_config') + '?tab=tools')
        else:
            form = ToolCredentialForm(request.POST, instance=tool_credential, tool=tool)
            if form.is_valid():
                form.save()
                return redirect(reverse('user_config') + '?tab=tools')
            else:
                return redirect(reverse('user_config') + '?tab=tools&error=credential_form_invalid')

    return redirect(reverse('user_config') + '?tab=tools')


@login_required
@require_POST
async def test_tool_connection(request, tool_id):
    # Wrap sync get_object_or_404
    tool = await sync_to_async(get_object_or_404)(Tool, id=tool_id,
                                                  user=request.user)

    try:
        auth_type = request.POST.get('auth_type', 'basic')
        username = request.POST.get('username', '')
        password = request.POST.get('password', '')
        token = request.POST.get('token', '')
        caldav_url = request.POST.get('caldav_url', '')

        # Sync function for get_or_create and initial setup
        async def create_credential_sync():
            temp_credential, created = await sync_to_async(ToolCredential.objects.get_or_create)(
                user=request.user,
                tool=tool,
                defaults={
                    'auth_type': auth_type,
                    'username': username,
                    'password': password,
                    'token': token,
                    'config': {
                        'caldav_url': caldav_url,
                        'username': username,
                        'password': password
                    }
                }
            )
            return temp_credential, created

        temp_credential, created = await create_credential_sync()

        # Sync function for updating credential if not created
        def update_credential_sync(temp_credential, auth_type, username,
                                   password, token, caldav_url):
            if not created:
                temp_credential.auth_type = auth_type
                temp_credential.username = username
                temp_credential.password = password if password else temp_credential.password
                temp_credential.token = token if token else temp_credential.token
                temp_credential.config.update({
                    'caldav_url': caldav_url,
                    'username': username,
                    'password': password if password else temp_credential.config.get('password', '')
                })
                temp_credential.save()
            return temp_credential

        temp_credential = await sync_to_async(update_credential_sync)(temp_credential, auth_type, username, password, token, caldav_url)

        if not temp_credential:
            logger.error(f"Failed to create credential for tool {tool_id}")
            return JsonResponse({"status": "error", "message": _("Failed to create credential")})

        # Test connection
        if tool.tool_type == Tool.ToolType.MCP:
            # MCP connection test
            try:
                client = MCPClient(
                    endpoint=tool.endpoint, 
                    credential=temp_credential, 
                    transport_type=tool.transport_type,
                    user_id=request.user.id  # Pass user_id explicitly
                )
                tools = await client.alist_tools(force_refresh=True)

                # Sync function to store in DB
                def save_available_functions_sync(tool, tools):
                    tool.available_functions = {
                        f["name"]: f for f in tools  # key = remote function name
                    }
                    tool.save(update_fields=["available_functions", "updated_at"])

                await sync_to_async(save_available_functions_sync)(tool, tools)

                tool_count = len(tools)
                if tool_count == 0:
                    return JsonResponse({
                        "status": "success", 
                        "message": _("Success connecting - No tools found"),
                        "tools": []
                    })
                else:
                    message_str = ngettext(
                        "Success connecting - %(count)d tool found",
                        "Success connecting - %(count)d tools found",
                        tool_count
                    ) % {"count": tool_count}
                    return JsonResponse({
                        "status": "success", 
                        "message": message_str,
                        "tools": tools
                    })

            except Exception as e:
                logger.error(f"MCP connection error for tool {tool_id}: {e}")
                return JsonResponse({
                    "status": "error",
                    "message": _("MCP connection error: %(err)s") % {"err": e}
                })

        elif tool.tool_type == Tool.ToolType.BUILTIN:
            async def builtin_test_sync(tool, request):
                metadata = get_metadata(tool.python_path)

                # Get the test function
                test_function = metadata.get('test_function')
                test_function_args = metadata.get('test_function_args')

                # Build the args
                args = []
                for arg in test_function_args:
                    if arg == 'user':
                        args.append(request.user)
                    elif arg == 'tool_id':
                        args.append(tool.id)
                    else:
                        args.append(None)

                # Call the function
                result = await test_function(*args)
                return result

            result = await builtin_test_sync(tool, request)
            return JsonResponse(result) if isinstance(result, dict) else JsonResponse({"status": "error", "message": str(result)})

        else:
            # Pour les autres types d'outils
            return JsonResponse({"status": "not_implemented", "message": _("No test implemented for this tool type")})

    except Exception as e:
        logger.error(f"Unexpected error in test_tool_connection for {tool_id}: {e}")
        return JsonResponse({"status": "error", "message": str(e)})
