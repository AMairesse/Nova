# Nova - User Settings App

This django app is dedicated to the user settings.

## Project layout

```
Nova
├─ user_settings/                           # Dedicated Django app for the user settings
|  ├─ migrations/                           # Django model migration scripts
|  ├─ static/                               # Static files
|  |  └─ user_settings/                     # Static files for the user settings
|  |     └─ js/                             # JavaScript files
|  |        ├─ agent.js                     # JavaScript for the agent form page
|  │        ├─ dashboard_tabs.js            # JavaScript for the dashboard tabs
|  │        ├─ provider.js                  # JavaScript for the provider page
|  │        ├─ scheduled_task_form.js       # JavaScript for the scheduled task form
|  │        ├─ tool_configure.js            # JavaScript for the tool configuration page
|  │        └─ tool.js                      # JavaScript for the tool form page
|  ├─ templates/                            # HTML templates
|  |  ├─ includes/                          # HTML includes
|  │  |  └─ pagination.html                 # HTML for pagination
|  │  └─ user_settings/                     # HTML templates for the user settings
|  |     ├─ fragments/                      # HTML fragments
|  │     |  ├─ agent_table.html             # HTML for the agents table
|  │     │  ├─ general_form.html            # HTML for the general settings form
|  │     │  ├─ memory_form.html             # HTML for the memory form
|  │     │  ├─ provider_table.html          # HTML for the providers table
|  │     │  └─ tool_table.html              # HTML for the tools table
|  │     ├─ agent_confirm_delete.html       # HTML for the agent deletion confirmation
|  │     ├─ agent_form.html                 # HTML for the agent form
|  │     ├─ agent_list.html                 # HTML for the agents page
|  │     ├─ dashboard.html                  # HTML for the dashboard
|  │     ├─ general_form.html               # HTML for the general settings form
|  │     ├─ memory_form.html                # HTML for the memory settings form
|  │     ├─ provider_confirm_delete.html    # HTML for the provider deletion confirmation
|  │     ├─ provider_form.html              # HTML for the provider form
|  │     ├─ provider_list.html              # HTML for the providers page
|  │     ├─ tool_configure.html             # HTML for the tool configuration page
|  │     ├─ tool_confirm_delete.html        # HTML for the tool deletion confirmation
|  │     ├─ tool_form.html                  # HTML for the tool form
|  │     └─ tool_list.html                  # HTML for the tools page
|  ├─ views/                                # Python views
|  │  ├─ agent.py                           # Python view for the agents page
|  │  ├─ api_token.py                       # Python view for the API token
|  │  ├─ dashboard.py                       # Python view for the dashboard
|  │  ├─ general.py                         # Python view for the general settings
|  │  ├─ memory.py                          # Python view for the memory settings
|  │  ├─ provider.py                        # Python view for the providers page
|  │  └─ tool.py                            # Python view for the tools page
|  ├─ apps.py                               # Django apps
|  ├─ forms.py                              # Python forms
|  ├─ mixins.py                             # Django mixins
|  ├─ README.md                             # This file
|  └─ urls.py                               # Django URLs
```