# Optimizing LLM cache usage when using Skills

LLM agents use a **prefix cache**: when a prompt starts with the same sequence of tokens as a previous request, the intermediate computations (key/value matrices) are reused [oai_citation:0‡claudecodecamp.com](https://www.claudecodecamp.com/p/how-prompt-caching-actually-works-in-claude-code#:~:text=window%20and%20its%20own%20independent,Opus%20session%27s%20cache%20is%20untouched). Any change to this prefix — including adding or removing a tool — invalidates that portion of the cache. When using *skills* that dynamically load tools, this behaviour can lead to a significant drop in cache hit rate.

## 1: Do not modify the tool list during the session

The most important rule is **to avoid adding or removing tools mid‑conversation**. Tool definitions are sent before the user messages; they therefore form part of the cached prefix. According to Claude Code engineers, adding just one MCP tool changes the prefix and forces the model to reprocess the entire history [oai_citation:1‡claudecodecamp.com](https://www.claudecodecamp.com/p/how-prompt-caching-actually-works-in-claude-code#:~:text=4,session).  

To keep a stable prefix, load all the necessary tools during initialization and don’t change `request.tools` afterwards. If a mode or feature requires different tools, it’s better to model that state through messages or tool calls (see point 3) rather than changing the tool list.

## 2: Prefer stubs with `defer_loading` for rarely used tools

Claude Code and the Anthropic API allow you to include **tool stubs**: each entry in `request.tools` contains only the tool name, a brief description and a `defer_loading: true` flag. When a tool is truly needed, the model can call a `ToolSearch` tool to fetch the full definition into a message, without modifying the tool list.  
This keeps the prefix identical for all users, even when the agent has lots of capabilities: only a few tokens (the stub) appear in the prefix, while the complete schema is loaded dynamically [oai_citation:2‡claudecodecamp.com](https://www.claudecodecamp.com/p/how-prompt-caching-actually-works-in-claude-code#:~:text=6,removing%20them).

## 3: Design state transitions as tools

In Claude Code, activating a special mode (for example a *planning mode*) is modelled through tools instead of by modifying the tool list. When the user enters planning mode, the request includes tools like `EnterPlanMode` and `ExitPlanMode`, and the system sends a message telling the model it is in planning mode. Tool definitions therefore remain identical from one request to the next [oai_citation:3‡claudecodecamp.com](https://www.claudecodecamp.com/p/how-prompt-caching-actually-works-in-claude-code#:~:text=The%20cache,plan%20mode%2C%20don%27t%20write%20files).  
This approach can be generalized: rather than loading and unloading different skills, add dedicated tools that trigger a mode or capability, and pass the necessary instructions via messages, so the cache is not broken.

## 4: Progressive disclosure of skills

Anthropic’s **skills** follow a *progressive disclosure* principle. Each skill contains a `SKILL.md` file whose metadata (name and description) alone is injected into the initial prompt [oai_citation:4‡claude.com](https://claude.com/blog/equipping-agents-for-the-real-world-with-agent-skills#:~:text=At%20its%20simplest%2C%20a%20skill,skill%20into%20its%20system%20prompt). The skill’s detailed content is loaded only when the agent needs it, for instance by reading `SKILL.md` or files referenced through a generic file‑reading tool. This avoids having to include long instructions in `request.tools` and preserves the stability of the prefix.

## 5: Use generic tools rather than specific tools

Many skills provide code (Python scripts, utilities) to perform operations. Instead of turning each into a separate tool, it is often more efficient to provide **generic code‑execution or file‑reading tools**. The skill can then be stored in the file system, and the agent executes the script via the generic tool (`bash`/`python`) without changing the tool list [oai_citation:5‡claude.com](https://claude.com/blog/equipping-agents-for-the-real-world-with-agent-skills#:~:text=Skills%20and%20code%20execution). This reduces the number of tools and minimizes changes to the prefix.

## 6: Update state via messages

When contextual information changes (time, application state, etc.), you should not modify the prompt or tools to indicate it. Instead, **pass updates via messages** (e.g. in the `messages` section of a request). Anthropic recommends using tags such as `<system-reminder>` inside messages, which leaves the cached part of the prompt untouched and preserves the cache [oai_citation:6‡claudecodecamp.com](https://www.claudecodecamp.com/p/how-prompt-caching-actually-works-in-claude-code#:~:text=The%20cache,plan%20mode%2C%20don%27t%20write%20files).

## 7: Advanced caches for large skills

More sophisticated caching solutions, such as **CacheBlend**, pre‑cache skill files and allow their pre‑computed key/value states to be concatenated at any position in the prompt. This approach yields a cache hit rate of 63 % to 85 % on skill content [oai_citation:7‡tensormesh.ai](https://www.tensormesh.ai/blog-posts/agent-skills-caching-cacheblend-llm-cache-hit-rates#:~:text=We%20propose%20a%20strategy%20for,related%20content). However, these solutions require finer control over caching and may involve specialised cache providers.

## Summary

- **Prefix stability:** never modify the tool list during the conversation [oai_citation:8‡claudecodecamp.com](https://www.claudecodecamp.com/p/how-prompt-caching-actually-works-in-claude-code#:~:text=4,session).  
- **Pre‑loading and stubs:** load all tools up front, use `defer_loading` for tools that are seldom used [oai_citation:9‡claudecodecamp.com](https://www.claudecodecamp.com/p/how-prompt-caching-actually-works-in-claude-code#:~:text=6,removing%20them).  
- **State transitions as tools:** design context changes through tools that don’t alter the prefix [oai_citation:10‡claudecodecamp.com](https://www.claudecodecamp.com/p/how-prompt-caching-actually-works-in-claude-code#:~:text=The%20cache,plan%20mode%2C%20don%27t%20write%20files).  
- **Progressive disclosure:** inject only a skill’s metadata into the initial prompt and load the rest on demand [oai_citation:11‡claude.com](https://claude.com/blog/equipping-agents-for-the-real-world-with-agent-skills#:~:text=At%20its%20simplest%2C%20a%20skill,skill%20into%20its%20system%20prompt).  
- **Generic tools:** prefer tools like `bash`/`python` to execute skill‑provided code instead of adding specific tools [oai_citation:12‡claude.com](https://claude.com/blog/equipping-agents-for-the-real-world-with-agent-skills#:~:text=Skills%20and%20code%20execution).  
- **Updates via messages:** pass changing information through messages rather than modifying the prompt [oai_citation:13‡claudecodecamp.com](https://www.claudecodecamp.com/p/how-prompt-caching-actually-works-in-claude-code#:~:text=The%20cache,plan%20mode%2C%20don%27t%20write%20files).  
- **Advanced caches:** for specific needs, explore solutions like CacheBlend that pre‑cache skills [oai_citation:14‡tensormesh.ai](https://www.tensormesh.ai/blog-posts/agent-skills-caching-cacheblend-llm-cache-hit-rates#:~:text=We%20propose%20a%20strategy%20for,related%20content).

By applying these strategies, agents that use skills can maintain a high cache hit rate, thus reducing session cost and latency.