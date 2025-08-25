# Nova - How to set up your own agents

# 1. Create an LLM provider

You need to create at least one LLM provider before you can create an agent.
If you can run your owns models (e.g., Ollama, LM Studio), I recommend using "qwen3-30b-a3b-2507". It's a great generalist model that can use tools and sub-agents efficiently, and it's small enough so that with flash attention you should be able to run up to 50 000 tokens of context on a 16 GB GPU.

For example:

| | |
| --- | --- |
| Name | ```LMStudio - Qwen3 30b``` |
| Type | ```LMStudio``` |
| Model | ```qwen/qwen3-30b-a3b-2507``` |
| Base URL | ```http://host.docker.internal:1234/v1``` (if served on the host machine running docker)|
| Max context tokens | ```50000``` (do not forget to set it in LMStudio and activate "flash attention")|
| | |

If you don't have the means to run locally then the best option is to use [openrouter.ai](https://openrouter.ai/) so that you can use the best LLM for each task.

For example:

| | |
| --- | --- |
| Name | ```Openrouter - GPT-5-mini``` |
| Type | ```OpenAI``` |
| Model | ```openai/gpt-5-mini``` |
| API key | ```enter your API key``` |
| Base URL | ```https://openrouter.ai/api/v1``` |
| Max context tokens | ```400000``` |
| | |

# 2. Create your tools

Add the defaults tools to your Nova workspace:
- ```Date / Time```: useful for almost all agents
- ```Browser```: useful for the webbrowsing agent

Configure your private tool :
- ```CalDav```: useful for the caldav agent, add it and go to the "Configure" panel to set the URL, username and password.


# 3. Create your agents

Nova's secret is to split the work from a generalist agent to a fleet of specific ones.
Each agent in a given thread will keep its own context and can use tools as needed.
The generalist agent will be your “main” agent that will delegate to the specific agents when needed.

## 3.1 Create the internet browser agent

| | |
| --- | --- |
| Name | ```Internet Agent``` |
| Provider | ```LMStudio - Qwen3 30b``` if you have access to a GPU / ```Openrouter - GPT-5-mini``` if you use openrouter.ai |
| Prompt |  ```You are an AI Agent specialized in retrieving information from the internet.If a website is not responding or return an error then stop trying an inform the user.```|
| Recursion limit | ```100``` (webbrowsing can require multiple tool calls for a single user request) |
| Use as a tool | ```Yes``` |
| Tool description | ```Use this agent to retrieve information from the internet.```|
|Associated tools | ```Date / Time``` and ```Browser``` |
| | |

## 3.2 Create the Calendar agent

| | |
| --- | --- |
| Name | ```Calendar Agent``` |
| Provider | ```LMStudio - Qwen3 30b``` if you have access to a GPU / ```Openrouter - GPT-5-mini``` if you use openrouter.ai |
| Prompt |  ```You are an AI Agent specialized in the management of the user's calendar. You use tools to access the calendar. Unless explicitly stated you do not need to specify which calendar to look into. You have read-only access to the calendar.```|
| Recursion limit | ```25``` |
| Use as a tool | ```Yes``` |
| Tool description | ```Use this agent to retrieve information from the user's calendar. The calendar's access is read-only.```|
|Associated tools | ```Date / Time``` and ```CalDav``` |
| | |

## 3.3 Create the main agent

| | |
| --- | --- |
| Name | ```Nova``` |
| Provider | ```LMStudio - Qwen3 30b``` if you have access to a GPU / ```Openrouter - GPT-5-mini``` if you use openrouter.ai |
| Prompt |  ```You are an AI Agent named Nova. You have access to tools and other agent to answer to the user. Do not lie about your abilities and do not offer to do something you are not able to do with the tools at your disposal. You answer the user in the language he used by default. Use markdown for your responses. Unless the user ask for a detailed answer you provide useful answer without too much detail.```|
| Recursion limit | ```25``` |
| Use as a tool | ```No``` |
| Associated tools | ```Date / Time``` |
| Agents as tools | ```Internet Agent``` and ```Calendar Agent``` |
| | |

# 4. Run your agent

Click on the Nova icon in the top left corner and start a conversation with your agent.

 