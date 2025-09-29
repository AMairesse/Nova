# Checkpointers[¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#checkpointers "Permanent link")

Classes:

| Name | Description |
| --- | --- |
| `[CheckpointMetadata](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.CheckpointMetadata "<code class="doc-symbol doc-symbol-heading doc-symbol-class"></code>            <span class="doc doc-object-name doc-class-name">CheckpointMetadata</span> (<code>langgraph.checkpoint.base.CheckpointMetadata</code>)")` | 
Metadata associated with a checkpoint.



 |
| `[Checkpoint](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.Checkpoint "<code class="doc-symbol doc-symbol-heading doc-symbol-class"></code>            <span class="doc doc-object-name doc-class-name">Checkpoint</span> (<code>langgraph.checkpoint.base.Checkpoint</code>)")` | 

State snapshot at a given point in time.



 |
| `[BaseCheckpointSaver](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.BaseCheckpointSaver "<code class="doc-symbol doc-symbol-heading doc-symbol-class"></code>            <span class="doc doc-object-name doc-class-name">BaseCheckpointSaver</span> (<code>langgraph.checkpoint.base.BaseCheckpointSaver</code>)")` | 

Base class for creating a graph checkpointer.



 |

Functions:

| Name | Description |
| --- | --- |
| `[create_checkpoint](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.create_checkpoint "<code class="doc-symbol doc-symbol-heading doc-symbol-function"></code>            <span class="doc doc-object-name doc-function-name">create_checkpoint</span> (<code>langgraph.checkpoint.base.create_checkpoint</code>)")` | 
Create a checkpoint for the given channels.



 |

## CheckpointMetadata [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.CheckpointMetadata "Permanent link")

Bases: `[TypedDict](https://docs.python.org/3/library/typing.html#typing.TypedDict "<code>typing.TypedDict</code>")`

Metadata associated with a checkpoint.

Attributes:

| Name | Type | Description |
| --- | --- | --- |
| `[source](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.CheckpointMetadata.source "<code class="doc-symbol doc-symbol-heading doc-symbol-attribute"></code>            <span class="doc doc-object-name doc-attribute-name">source</span> <span class="doc doc-labels"> <small class="doc doc-label doc-label-instance-attribute"><code>instance-attribute</code></small> </span> (<code>langgraph.checkpoint.base.CheckpointMetadata.source</code>)")` | `[Literal](https://docs.python.org/3/library/typing.html#typing.Literal "<code>typing.Literal</code>")['input', 'loop', 'update', 'fork']` | 
The source of the checkpoint.



 |
| `[step](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.CheckpointMetadata.step "<code class="doc-symbol doc-symbol-heading doc-symbol-attribute"></code>            <span class="doc doc-object-name doc-attribute-name">step</span> <span class="doc doc-labels"> <small class="doc doc-label doc-label-instance-attribute"><code>instance-attribute</code></small> </span> (<code>langgraph.checkpoint.base.CheckpointMetadata.step</code>)")` | `[int](https://docs.python.org/3/library/functions.html#int)` | 

The step number of the checkpoint.



 |
| `[parents](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.CheckpointMetadata.parents "<code class="doc-symbol doc-symbol-heading doc-symbol-attribute"></code>            <span class="doc doc-object-name doc-attribute-name">parents</span> <span class="doc doc-labels"> <small class="doc doc-label doc-label-instance-attribute"><code>instance-attribute</code></small> </span> (<code>langgraph.checkpoint.base.CheckpointMetadata.parents</code>)")` | `[dict](https://docs.python.org/3/library/stdtypes.html#dict)[[str](https://docs.python.org/3/library/stdtypes.html#str), [str](https://docs.python.org/3/library/stdtypes.html#str)]` | 

The IDs of the parent checkpoints.



 |

### source `instance-attribute` [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.CheckpointMetadata.source "Permanent link")

```
source: Literal['input', 'loop', 'update', 'fork']

```

The source of the checkpoint.

-   "input": The checkpoint was created from an input to invoke/stream/batch.
-   "loop": The checkpoint was created from inside the pregel loop.
-   "update": The checkpoint was created from a manual state update.
-   "fork": The checkpoint was created as a copy of another checkpoint.

### step `instance-attribute` [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.CheckpointMetadata.step "Permanent link")

```
step: int

```

The step number of the checkpoint.

\-1 for the first "input" checkpoint. 0 for the first "loop" checkpoint. ... for the nth checkpoint afterwards.

### parents `instance-attribute` [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.CheckpointMetadata.parents "Permanent link")

```
parents: dict[str, str]

```

The IDs of the parent checkpoints.

Mapping from checkpoint namespace to checkpoint ID.

## Checkpoint [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.Checkpoint "Permanent link")

Bases: `[TypedDict](https://docs.python.org/3/library/typing.html#typing.TypedDict "<code>typing.TypedDict</code>")`

State snapshot at a given point in time.

Attributes:

| Name | Type | Description |
| --- | --- | --- |
| `[v](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.Checkpoint.v "<code class="doc-symbol doc-symbol-heading doc-symbol-attribute"></code>            <span class="doc doc-object-name doc-attribute-name">v</span> <span class="doc doc-labels"> <small class="doc doc-label doc-label-instance-attribute"><code>instance-attribute</code></small> </span> (<code>langgraph.checkpoint.base.Checkpoint.v</code>)")` | `[int](https://docs.python.org/3/library/functions.html#int)` | 
The version of the checkpoint format. Currently 1.



 |
| `[id](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.Checkpoint.id "<code class="doc-symbol doc-symbol-heading doc-symbol-attribute"></code>            <span class="doc doc-object-name doc-attribute-name">id</span> <span class="doc doc-labels"> <small class="doc doc-label doc-label-instance-attribute"><code>instance-attribute</code></small> </span> (<code>langgraph.checkpoint.base.Checkpoint.id</code>)")` | `[str](https://docs.python.org/3/library/stdtypes.html#str)` | 

The ID of the checkpoint. This is both unique and monotonically



 |
| `[ts](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.Checkpoint.ts "<code class="doc-symbol doc-symbol-heading doc-symbol-attribute"></code>            <span class="doc doc-object-name doc-attribute-name">ts</span> <span class="doc doc-labels"> <small class="doc doc-label doc-label-instance-attribute"><code>instance-attribute</code></small> </span> (<code>langgraph.checkpoint.base.Checkpoint.ts</code>)")` | `[str](https://docs.python.org/3/library/stdtypes.html#str)` | 

The timestamp of the checkpoint in ISO 8601 format.



 |
| `[channel_values](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.Checkpoint.channel_values "<code class="doc-symbol doc-symbol-heading doc-symbol-attribute"></code>            <span class="doc doc-object-name doc-attribute-name">channel_values</span> <span class="doc doc-labels"> <small class="doc doc-label doc-label-instance-attribute"><code>instance-attribute</code></small> </span> (<code>langgraph.checkpoint.base.Checkpoint.channel_values</code>)")` | `[dict](https://docs.python.org/3/library/stdtypes.html#dict)[[str](https://docs.python.org/3/library/stdtypes.html#str), [Any](https://docs.python.org/3/library/typing.html#typing.Any "<code>typing.Any</code>")]` | 

The values of the channels at the time of the checkpoint.



 |
| `[channel_versions](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.Checkpoint.channel_versions "<code class="doc-symbol doc-symbol-heading doc-symbol-attribute"></code>            <span class="doc doc-object-name doc-attribute-name">channel_versions</span> <span class="doc doc-labels"> <small class="doc doc-label doc-label-instance-attribute"><code>instance-attribute</code></small> </span> (<code>langgraph.checkpoint.base.Checkpoint.channel_versions</code>)")` | `ChannelVersions` | 

The versions of the channels at the time of the checkpoint.



 |
| `[versions_seen](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.Checkpoint.versions_seen "<code class="doc-symbol doc-symbol-heading doc-symbol-attribute"></code>            <span class="doc doc-object-name doc-attribute-name">versions_seen</span> <span class="doc doc-labels"> <small class="doc doc-label doc-label-instance-attribute"><code>instance-attribute</code></small> </span> (<code>langgraph.checkpoint.base.Checkpoint.versions_seen</code>)")` | `[dict](https://docs.python.org/3/library/stdtypes.html#dict)[[str](https://docs.python.org/3/library/stdtypes.html#str), ChannelVersions]` | 

Map from node ID to map from channel name to version seen.



 |
| `[updated_channels](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.Checkpoint.updated_channels "<code class="doc-symbol doc-symbol-heading doc-symbol-attribute"></code>            <span class="doc doc-object-name doc-attribute-name">updated_channels</span> <span class="doc doc-labels"> <small class="doc doc-label doc-label-instance-attribute"><code>instance-attribute</code></small> </span> (<code>langgraph.checkpoint.base.Checkpoint.updated_channels</code>)")` | `[list](https://docs.python.org/3/library/stdtypes.html#list)[[str](https://docs.python.org/3/library/stdtypes.html#str)] | None` | 

The channels that were updated in this checkpoint.



 |

### v `instance-attribute` [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.Checkpoint.v "Permanent link")

```
v: int

```

The version of the checkpoint format. Currently 1.

### id `instance-attribute` [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.Checkpoint.id "Permanent link")

```
id: str

```

The ID of the checkpoint. This is both unique and monotonically increasing, so can be used for sorting checkpoints from first to last.

### ts `instance-attribute` [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.Checkpoint.ts "Permanent link")

```
ts: str

```

The timestamp of the checkpoint in ISO 8601 format.

### channel\_values `instance-attribute` [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.Checkpoint.channel_values "Permanent link")

```
channel_values: dict[str, Any]

```

The values of the channels at the time of the checkpoint. Mapping from channel name to deserialized channel snapshot value.

### channel\_versions `instance-attribute` [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.Checkpoint.channel_versions "Permanent link")

```
channel_versions: ChannelVersions

```

The versions of the channels at the time of the checkpoint. The keys are channel names and the values are monotonically increasing version strings for each channel.

### versions\_seen `instance-attribute` [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.Checkpoint.versions_seen "Permanent link")

```
versions_seen: dict[str, ChannelVersions]

```

Map from node ID to map from channel name to version seen. This keeps track of the versions of the channels that each node has seen. Used to determine which nodes to execute next.

### updated\_channels `instance-attribute` [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.Checkpoint.updated_channels "Permanent link")

```
updated_channels: list[str] | None

```

The channels that were updated in this checkpoint.

## BaseCheckpointSaver [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.BaseCheckpointSaver "Permanent link")

Bases: `[Generic](https://docs.python.org/3/library/typing.html#typing.Generic "<code>typing.Generic</code>")[V]`

Base class for creating a graph checkpointer.

Checkpointers allow LangGraph agents to persist their state within and across multiple interactions.

Attributes:

| Name | Type | Description |
| --- | --- | --- |
| `serde` | `[SerializerProtocol](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.serde.base.SerializerProtocol "<code class="doc-symbol doc-symbol-heading doc-symbol-class"></code>            <span class="doc doc-object-name doc-class-name">SerializerProtocol</span> (<code>langgraph.checkpoint.serde.base.SerializerProtocol</code>)")` | 
Serializer for encoding/decoding checkpoints.



 |

Note

When creating a custom checkpoint saver, consider implementing async versions to avoid blocking the main thread.

Methods:

| Name | Description |
| --- | --- |
| `[get](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.BaseCheckpointSaver.get "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">get</span> (<code>langgraph.checkpoint.base.BaseCheckpointSaver.get</code>)")` | 
Fetch a checkpoint using the given configuration.



 |
| `[get_tuple](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.BaseCheckpointSaver.get_tuple "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">get_tuple</span> (<code>langgraph.checkpoint.base.BaseCheckpointSaver.get_tuple</code>)")` | 

Fetch a checkpoint tuple using the given configuration.



 |
| `[list](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.BaseCheckpointSaver.list "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">list</span> (<code>langgraph.checkpoint.base.BaseCheckpointSaver.list</code>)")` | 

List checkpoints that match the given criteria.



 |
| `[put](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.BaseCheckpointSaver.put "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">put</span> (<code>langgraph.checkpoint.base.BaseCheckpointSaver.put</code>)")` | 

Store a checkpoint with its configuration and metadata.



 |
| `[put_writes](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.BaseCheckpointSaver.put_writes "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">put_writes</span> (<code>langgraph.checkpoint.base.BaseCheckpointSaver.put_writes</code>)")` | 

Store intermediate writes linked to a checkpoint.



 |
| `[delete_thread](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.BaseCheckpointSaver.delete_thread "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">delete_thread</span> (<code>langgraph.checkpoint.base.BaseCheckpointSaver.delete_thread</code>)")` | 

Delete all checkpoints and writes associated with a specific thread ID.



 |
| `[aget](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.BaseCheckpointSaver.aget "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">aget</span> <span class="doc doc-labels"> <small class="doc doc-label doc-label-async"><code>async</code></small> </span> (<code>langgraph.checkpoint.base.BaseCheckpointSaver.aget</code>)")` | 

Asynchronously fetch a checkpoint using the given configuration.



 |
| `[aget_tuple](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.BaseCheckpointSaver.aget_tuple "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">aget_tuple</span> <span class="doc doc-labels"> <small class="doc doc-label doc-label-async"><code>async</code></small> </span> (<code>langgraph.checkpoint.base.BaseCheckpointSaver.aget_tuple</code>)")` | 

Asynchronously fetch a checkpoint tuple using the given configuration.



 |
| `[alist](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.BaseCheckpointSaver.alist "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">alist</span> <span class="doc doc-labels"> <small class="doc doc-label doc-label-async"><code>async</code></small> </span> (<code>langgraph.checkpoint.base.BaseCheckpointSaver.alist</code>)")` | 

Asynchronously list checkpoints that match the given criteria.



 |
| `[aput](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.BaseCheckpointSaver.aput "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">aput</span> <span class="doc doc-labels"> <small class="doc doc-label doc-label-async"><code>async</code></small> </span> (<code>langgraph.checkpoint.base.BaseCheckpointSaver.aput</code>)")` | 

Asynchronously store a checkpoint with its configuration and metadata.



 |
| `[aput_writes](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.BaseCheckpointSaver.aput_writes "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">aput_writes</span> <span class="doc doc-labels"> <small class="doc doc-label doc-label-async"><code>async</code></small> </span> (<code>langgraph.checkpoint.base.BaseCheckpointSaver.aput_writes</code>)")` | 

Asynchronously store intermediate writes linked to a checkpoint.



 |
| `[adelete_thread](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.BaseCheckpointSaver.adelete_thread "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">adelete_thread</span> <span class="doc doc-labels"> <small class="doc doc-label doc-label-async"><code>async</code></small> </span> (<code>langgraph.checkpoint.base.BaseCheckpointSaver.adelete_thread</code>)")` | 

Delete all checkpoints and writes associated with a specific thread ID.



 |
| `[get_next_version](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.BaseCheckpointSaver.get_next_version "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">get_next_version</span> (<code>langgraph.checkpoint.base.BaseCheckpointSaver.get_next_version</code>)")` | 

Generate the next version ID for a channel.



 |

### config\_specs `property` [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.BaseCheckpointSaver.config_specs "Permanent link")

```
config_specs: list

```

Define the configuration options for the checkpoint saver.

Returns:

| Name | Type | Description |
| --- | --- | --- |
| `list` | `[list](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.BaseCheckpointSaver.list "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">list</span> (<code>langgraph.checkpoint.base.BaseCheckpointSaver.list</code>)")` | 
List of configuration field specs.



 |

### get [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.BaseCheckpointSaver.get "Permanent link")

```
get(config: RunnableConfig) -> Checkpoint | None

```

Fetch a checkpoint using the given configuration.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `config` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>")` | 
Configuration specifying which checkpoint to retrieve.



 | _required_ |

Returns:

| Type | Description |
| --- | --- |
| `[Checkpoint](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.Checkpoint "<code class="doc-symbol doc-symbol-heading doc-symbol-class"></code>            <span class="doc doc-object-name doc-class-name">Checkpoint</span> (<code>langgraph.checkpoint.base.Checkpoint</code>)") | None` | 
Optional\[Checkpoint\]: The requested checkpoint, or None if not found.



 |

### get\_tuple [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.BaseCheckpointSaver.get_tuple "Permanent link")

```
get_tuple(config: RunnableConfig) -> CheckpointTuple | None

```

Fetch a checkpoint tuple using the given configuration.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `config` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>")` | 
Configuration specifying which checkpoint to retrieve.



 | _required_ |

Returns:

| Type | Description |
| --- | --- |
| `CheckpointTuple | None` | 
Optional\[CheckpointTuple\]: The requested checkpoint tuple, or None if not found.



 |

Raises:

| Type | Description |
| --- | --- |
| `[NotImplementedError](https://docs.python.org/3/library/exceptions.html#NotImplementedError)` | 
Implement this method in your custom checkpoint saver.



 |

### list [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.BaseCheckpointSaver.list "Permanent link")

```
list(
    config: RunnableConfig | None,
    *,
    filter: dict[str, Any] | None = None,
    before: RunnableConfig | None = None,
    limit: int | None = None
) -> Iterator[CheckpointTuple]

```

List checkpoints that match the given criteria.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `config` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>") | None` | 
Base configuration for filtering checkpoints.



 | _required_ |
| `filter` | `[dict](https://docs.python.org/3/library/stdtypes.html#dict)[[str](https://docs.python.org/3/library/stdtypes.html#str), [Any](https://docs.python.org/3/library/typing.html#typing.Any "<code>typing.Any</code>")] | None` | 

Additional filtering criteria.



 | `None` |
| `before` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>") | None` | 

List checkpoints created before this configuration.



 | `None` |
| `limit` | `[int](https://docs.python.org/3/library/functions.html#int) | None` | 

Maximum number of checkpoints to return.



 | `None` |

Returns:

| Type | Description |
| --- | --- |
| `[Iterator](https://docs.python.org/3/library/collections.abc.html#collections.abc.Iterator "<code>collections.abc.Iterator</code>")[CheckpointTuple]` | 
Iterator\[CheckpointTuple\]: Iterator of matching checkpoint tuples.



 |

Raises:

| Type | Description |
| --- | --- |
| `[NotImplementedError](https://docs.python.org/3/library/exceptions.html#NotImplementedError)` | 
Implement this method in your custom checkpoint saver.



 |

### put [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.BaseCheckpointSaver.put "Permanent link")

```
put(
    config: RunnableConfig,
    checkpoint: Checkpoint,
    metadata: CheckpointMetadata,
    new_versions: ChannelVersions,
) -> RunnableConfig

```

Store a checkpoint with its configuration and metadata.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `config` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>")` | 
Configuration for the checkpoint.



 | _required_ |
| `checkpoint` | `[Checkpoint](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.Checkpoint "<code class="doc-symbol doc-symbol-heading doc-symbol-class"></code>            <span class="doc doc-object-name doc-class-name">Checkpoint</span> (<code>langgraph.checkpoint.base.Checkpoint</code>)")` | 

The checkpoint to store.



 | _required_ |
| `metadata` | `[CheckpointMetadata](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.CheckpointMetadata "<code class="doc-symbol doc-symbol-heading doc-symbol-class"></code>            <span class="doc doc-object-name doc-class-name">CheckpointMetadata</span> (<code>langgraph.checkpoint.base.CheckpointMetadata</code>)")` | 

Additional metadata for the checkpoint.



 | _required_ |
| `new_versions` | `ChannelVersions` | 

New channel versions as of this write.



 | _required_ |

Returns:

| Name | Type | Description |
| --- | --- | --- |
| `RunnableConfig` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>")` | 
Updated configuration after storing the checkpoint.



 |

Raises:

| Type | Description |
| --- | --- |
| `[NotImplementedError](https://docs.python.org/3/library/exceptions.html#NotImplementedError)` | 
Implement this method in your custom checkpoint saver.



 |

### put\_writes [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.BaseCheckpointSaver.put_writes "Permanent link")

```
put_writes(
    config: RunnableConfig,
    writes: Sequence[tuple[str, Any]],
    task_id: str,
    task_path: str = "",
) -> None

```

Store intermediate writes linked to a checkpoint.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `config` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>")` | 
Configuration of the related checkpoint.



 | _required_ |
| `writes` | `[Sequence](https://docs.python.org/3/library/collections.abc.html#collections.abc.Sequence "<code>collections.abc.Sequence</code>")[[tuple](https://docs.python.org/3/library/stdtypes.html#tuple)[[str](https://docs.python.org/3/library/stdtypes.html#str), [Any](https://docs.python.org/3/library/typing.html#typing.Any "<code>typing.Any</code>")]]` | 

List of writes to store.



 | _required_ |
| `task_id` | `[str](https://docs.python.org/3/library/stdtypes.html#str)` | 

Identifier for the task creating the writes.



 | _required_ |
| `task_path` | `[str](https://docs.python.org/3/library/stdtypes.html#str)` | 

Path of the task creating the writes.



 | `''` |

Raises:

| Type | Description |
| --- | --- |
| `[NotImplementedError](https://docs.python.org/3/library/exceptions.html#NotImplementedError)` | 
Implement this method in your custom checkpoint saver.



 |

### delete\_thread [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.BaseCheckpointSaver.delete_thread "Permanent link")

```
delete_thread(thread_id: str) -> None

```

Delete all checkpoints and writes associated with a specific thread ID.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `thread_id` | `[str](https://docs.python.org/3/library/stdtypes.html#str)` | 
The thread ID whose checkpoints should be deleted.



 | _required_ |

### aget `async` [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.BaseCheckpointSaver.aget "Permanent link")

```
aget(config: RunnableConfig) -> Checkpoint | None

```

Asynchronously fetch a checkpoint using the given configuration.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `config` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>")` | 
Configuration specifying which checkpoint to retrieve.



 | _required_ |

Returns:

| Type | Description |
| --- | --- |
| `[Checkpoint](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.Checkpoint "<code class="doc-symbol doc-symbol-heading doc-symbol-class"></code>            <span class="doc doc-object-name doc-class-name">Checkpoint</span> (<code>langgraph.checkpoint.base.Checkpoint</code>)") | None` | 
Optional\[Checkpoint\]: The requested checkpoint, or None if not found.



 |

### aget\_tuple `async` [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.BaseCheckpointSaver.aget_tuple "Permanent link")

```
aget_tuple(
    config: RunnableConfig,
) -> CheckpointTuple | None

```

Asynchronously fetch a checkpoint tuple using the given configuration.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `config` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>")` | 
Configuration specifying which checkpoint to retrieve.



 | _required_ |

Returns:

| Type | Description |
| --- | --- |
| `CheckpointTuple | None` | 
Optional\[CheckpointTuple\]: The requested checkpoint tuple, or None if not found.



 |

Raises:

| Type | Description |
| --- | --- |
| `[NotImplementedError](https://docs.python.org/3/library/exceptions.html#NotImplementedError)` | 
Implement this method in your custom checkpoint saver.



 |

### alist `async` [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.BaseCheckpointSaver.alist "Permanent link")

```
alist(
    config: RunnableConfig | None,
    *,
    filter: dict[str, Any] | None = None,
    before: RunnableConfig | None = None,
    limit: int | None = None
) -> AsyncIterator[CheckpointTuple]

```

Asynchronously list checkpoints that match the given criteria.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `config` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>") | None` | 
Base configuration for filtering checkpoints.



 | _required_ |
| `filter` | `[dict](https://docs.python.org/3/library/stdtypes.html#dict)[[str](https://docs.python.org/3/library/stdtypes.html#str), [Any](https://docs.python.org/3/library/typing.html#typing.Any "<code>typing.Any</code>")] | None` | 

Additional filtering criteria for metadata.



 | `None` |
| `before` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>") | None` | 

List checkpoints created before this configuration.



 | `None` |
| `limit` | `[int](https://docs.python.org/3/library/functions.html#int) | None` | 

Maximum number of checkpoints to return.



 | `None` |

Returns:

| Type | Description |
| --- | --- |
| `[AsyncIterator](https://docs.python.org/3/library/collections.abc.html#collections.abc.AsyncIterator "<code>collections.abc.AsyncIterator</code>")[CheckpointTuple]` | 
AsyncIterator\[CheckpointTuple\]: Async iterator of matching checkpoint tuples.



 |

Raises:

| Type | Description |
| --- | --- |
| `[NotImplementedError](https://docs.python.org/3/library/exceptions.html#NotImplementedError)` | 
Implement this method in your custom checkpoint saver.



 |

### aput `async` [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.BaseCheckpointSaver.aput "Permanent link")

```
aput(
    config: RunnableConfig,
    checkpoint: Checkpoint,
    metadata: CheckpointMetadata,
    new_versions: ChannelVersions,
) -> RunnableConfig

```

Asynchronously store a checkpoint with its configuration and metadata.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `config` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>")` | 
Configuration for the checkpoint.



 | _required_ |
| `checkpoint` | `[Checkpoint](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.Checkpoint "<code class="doc-symbol doc-symbol-heading doc-symbol-class"></code>            <span class="doc doc-object-name doc-class-name">Checkpoint</span> (<code>langgraph.checkpoint.base.Checkpoint</code>)")` | 

The checkpoint to store.



 | _required_ |
| `metadata` | `[CheckpointMetadata](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.CheckpointMetadata "<code class="doc-symbol doc-symbol-heading doc-symbol-class"></code>            <span class="doc doc-object-name doc-class-name">CheckpointMetadata</span> (<code>langgraph.checkpoint.base.CheckpointMetadata</code>)")` | 

Additional metadata for the checkpoint.



 | _required_ |
| `new_versions` | `ChannelVersions` | 

New channel versions as of this write.



 | _required_ |

Returns:

| Name | Type | Description |
| --- | --- | --- |
| `RunnableConfig` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>")` | 
Updated configuration after storing the checkpoint.



 |

Raises:

| Type | Description |
| --- | --- |
| `[NotImplementedError](https://docs.python.org/3/library/exceptions.html#NotImplementedError)` | 
Implement this method in your custom checkpoint saver.



 |

### aput\_writes `async` [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.BaseCheckpointSaver.aput_writes "Permanent link")

```
aput_writes(
    config: RunnableConfig,
    writes: Sequence[tuple[str, Any]],
    task_id: str,
    task_path: str = "",
) -> None

```

Asynchronously store intermediate writes linked to a checkpoint.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `config` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>")` | 
Configuration of the related checkpoint.



 | _required_ |
| `writes` | `[Sequence](https://docs.python.org/3/library/collections.abc.html#collections.abc.Sequence "<code>collections.abc.Sequence</code>")[[tuple](https://docs.python.org/3/library/stdtypes.html#tuple)[[str](https://docs.python.org/3/library/stdtypes.html#str), [Any](https://docs.python.org/3/library/typing.html#typing.Any "<code>typing.Any</code>")]]` | 

List of writes to store.



 | _required_ |
| `task_id` | `[str](https://docs.python.org/3/library/stdtypes.html#str)` | 

Identifier for the task creating the writes.



 | _required_ |
| `task_path` | `[str](https://docs.python.org/3/library/stdtypes.html#str)` | 

Path of the task creating the writes.



 | `''` |

Raises:

| Type | Description |
| --- | --- |
| `[NotImplementedError](https://docs.python.org/3/library/exceptions.html#NotImplementedError)` | 
Implement this method in your custom checkpoint saver.



 |

### adelete\_thread `async` [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.BaseCheckpointSaver.adelete_thread "Permanent link")

```
adelete_thread(thread_id: str) -> None

```

Delete all checkpoints and writes associated with a specific thread ID.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `thread_id` | `[str](https://docs.python.org/3/library/stdtypes.html#str)` | 
The thread ID whose checkpoints should be deleted.



 | _required_ |

### get\_next\_version [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.BaseCheckpointSaver.get_next_version "Permanent link")

```
get_next_version(current: V | None, channel: None) -> V

```

Generate the next version ID for a channel.

Default is to use integer versions, incrementing by 1. If you override, you can use str/int/float versions, as long as they are monotonically increasing.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `current` | `V | None` | 
The current version identifier (int, float, or str).



 | _required_ |
| `channel` | `None` | 

Deprecated argument, kept for backwards compatibility.



 | _required_ |

Returns:

| Name | Type | Description |
| --- | --- | --- |
| `V` | `V` | 
The next version identifier, which must be increasing.



 |

## create\_checkpoint [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.create_checkpoint "Permanent link")

```
create_checkpoint(
    checkpoint: Checkpoint,
    channels: Mapping[str, ChannelProtocol] | None,
    step: int,
    *,
    id: str | None = None
) -> Checkpoint

```

Create a checkpoint for the given channels.

Classes:

| Name | Description |
| --- | --- |
| `[SerializerProtocol](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.serde.base.SerializerProtocol "<code class="doc-symbol doc-symbol-heading doc-symbol-class"></code>            <span class="doc doc-object-name doc-class-name">SerializerProtocol</span> (<code>langgraph.checkpoint.serde.base.SerializerProtocol</code>)")` | 
Protocol for serialization and deserialization of objects.



 |
| `[CipherProtocol](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.serde.base.CipherProtocol "<code class="doc-symbol doc-symbol-heading doc-symbol-class"></code>            <span class="doc doc-object-name doc-class-name">CipherProtocol</span> (<code>langgraph.checkpoint.serde.base.CipherProtocol</code>)")` | 

Protocol for encryption and decryption of data.



 |

## SerializerProtocol [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.serde.base.SerializerProtocol "Permanent link")

Bases: `UntypedSerializerProtocol`, `[Protocol](https://docs.python.org/3/library/typing.html#typing.Protocol "<code>typing.Protocol</code>")`

Protocol for serialization and deserialization of objects.

-   `dumps`: Serialize an object to bytes.
-   `dumps_typed`: Serialize an object to a tuple (type, bytes).
-   `loads`: Deserialize an object from bytes.
-   `loads_typed`: Deserialize an object from a tuple (type, bytes).

Valid implementations include the `pickle`, `json` and `orjson` modules.

## CipherProtocol [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.serde.base.CipherProtocol "Permanent link")

Bases: `[Protocol](https://docs.python.org/3/library/typing.html#typing.Protocol "<code>typing.Protocol</code>")`

Protocol for encryption and decryption of data. - `encrypt`: Encrypt plaintext. - `decrypt`: Decrypt ciphertext.

Methods:

| Name | Description |
| --- | --- |
| `[encrypt](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.serde.base.CipherProtocol.encrypt "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">encrypt</span> (<code>langgraph.checkpoint.serde.base.CipherProtocol.encrypt</code>)")` | 
Encrypt plaintext. Returns a tuple (cipher name, ciphertext).



 |
| `[decrypt](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.serde.base.CipherProtocol.decrypt "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">decrypt</span> (<code>langgraph.checkpoint.serde.base.CipherProtocol.decrypt</code>)")` | 

Decrypt ciphertext. Returns the plaintext.



 |

### encrypt [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.serde.base.CipherProtocol.encrypt "Permanent link")

```
encrypt(plaintext: bytes) -> tuple[str, bytes]

```

Encrypt plaintext. Returns a tuple (cipher name, ciphertext).

### decrypt [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.serde.base.CipherProtocol.decrypt "Permanent link")

```
decrypt(ciphername: str, ciphertext: bytes) -> bytes

```

Decrypt ciphertext. Returns the plaintext.

Classes:

| Name | Description |
| --- | --- |
| `[JsonPlusSerializer](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.serde.jsonplus.JsonPlusSerializer "<code class="doc-symbol doc-symbol-heading doc-symbol-class"></code>            <span class="doc doc-object-name doc-class-name">JsonPlusSerializer</span> (<code>langgraph.checkpoint.serde.jsonplus.JsonPlusSerializer</code>)")` | 
Serializer that uses ormsgpack, with a fallback to extended JSON serializer.



 |

## JsonPlusSerializer [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.serde.jsonplus.JsonPlusSerializer "Permanent link")

Bases: `[SerializerProtocol](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.serde.base.SerializerProtocol "<code class="doc-symbol doc-symbol-heading doc-symbol-class"></code>            <span class="doc doc-object-name doc-class-name">SerializerProtocol</span> (<code>langgraph.checkpoint.serde.base.SerializerProtocol</code>)")`

Serializer that uses ormsgpack, with a fallback to extended JSON serializer.

Classes:

| Name | Description |
| --- | --- |
| `[EncryptedSerializer](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.serde.encrypted.EncryptedSerializer "<code class="doc-symbol doc-symbol-heading doc-symbol-class"></code>            <span class="doc doc-object-name doc-class-name">EncryptedSerializer</span> (<code>langgraph.checkpoint.serde.encrypted.EncryptedSerializer</code>)")` | 
Serializer that encrypts and decrypts data using an encryption protocol.



 |

## EncryptedSerializer [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.serde.encrypted.EncryptedSerializer "Permanent link")

Bases: `[SerializerProtocol](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.serde.base.SerializerProtocol "<code class="doc-symbol doc-symbol-heading doc-symbol-class"></code>            <span class="doc doc-object-name doc-class-name">SerializerProtocol</span> (<code>langgraph.checkpoint.serde.base.SerializerProtocol</code>)")`

Serializer that encrypts and decrypts data using an encryption protocol.

Methods:

| Name | Description |
| --- | --- |
| `[dumps_typed](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.serde.encrypted.EncryptedSerializer.dumps_typed "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">dumps_typed</span> (<code>langgraph.checkpoint.serde.encrypted.EncryptedSerializer.dumps_typed</code>)")` | 
Serialize an object to a tuple (type, bytes) and encrypt the bytes.



 |
| `[from_pycryptodome_aes](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.serde.encrypted.EncryptedSerializer.from_pycryptodome_aes "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">from_pycryptodome_aes</span> <span class="doc doc-labels"> <small class="doc doc-label doc-label-classmethod"><code>classmethod</code></small> </span> (<code>langgraph.checkpoint.serde.encrypted.EncryptedSerializer.from_pycryptodome_aes</code>)")` | 

Create an EncryptedSerializer using AES encryption.



 |

### dumps\_typed [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.serde.encrypted.EncryptedSerializer.dumps_typed "Permanent link")

```
dumps_typed(obj: Any) -> tuple[str, bytes]

```

Serialize an object to a tuple (type, bytes) and encrypt the bytes.

### from\_pycryptodome\_aes `classmethod` [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.serde.encrypted.EncryptedSerializer.from_pycryptodome_aes "Permanent link")

```
from_pycryptodome_aes(
    serde: SerializerProtocol = JsonPlusSerializer(),
    **kwargs: Any
) -> EncryptedSerializer

```

Create an EncryptedSerializer using AES encryption.

Classes:

| Name | Description |
| --- | --- |
| `[InMemorySaver](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.memory.InMemorySaver "<code class="doc-symbol doc-symbol-heading doc-symbol-class"></code>            <span class="doc doc-object-name doc-class-name">InMemorySaver</span> (<code>langgraph.checkpoint.memory.InMemorySaver</code>)")` | 
An in-memory checkpoint saver.



 |
| `[PersistentDict](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.memory.PersistentDict "<code class="doc-symbol doc-symbol-heading doc-symbol-class"></code>            <span class="doc doc-object-name doc-class-name">PersistentDict</span> (<code>langgraph.checkpoint.memory.PersistentDict</code>)")` | 

Persistent dictionary with an API compatible with shelve and anydbm.



 |

## InMemorySaver [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.memory.InMemorySaver "Permanent link")

Bases: `[BaseCheckpointSaver](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.BaseCheckpointSaver "<code class="doc-symbol doc-symbol-heading doc-symbol-class"></code>            <span class="doc doc-object-name doc-class-name">BaseCheckpointSaver</span> (<code>langgraph.checkpoint.base.BaseCheckpointSaver</code>)")[[str](https://docs.python.org/3/library/stdtypes.html#str)]`, `[AbstractContextManager](https://docs.python.org/3/library/contextlib.html#contextlib.AbstractContextManager "<code>contextlib.AbstractContextManager</code>")`, `[AbstractAsyncContextManager](https://docs.python.org/3/library/contextlib.html#contextlib.AbstractAsyncContextManager "<code>contextlib.AbstractAsyncContextManager</code>")`

An in-memory checkpoint saver.

This checkpoint saver stores checkpoints in memory using a defaultdict.

Note

Only use `InMemorySaver` for debugging or testing purposes. For production use cases we recommend installing [langgraph-checkpoint-postgres](https://pypi.org/project/langgraph-checkpoint-postgres/) and using `PostgresSaver` / `AsyncPostgresSaver`.

If you are using the LangGraph Platform, no checkpointer needs to be specified. The correct managed checkpointer will be used automatically.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `serde` | `[SerializerProtocol](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.serde.base.SerializerProtocol "<code class="doc-symbol doc-symbol-heading doc-symbol-class"></code>            <span class="doc doc-object-name doc-class-name">SerializerProtocol</span> (<code>langgraph.checkpoint.base.SerializerProtocol</code>)") | None` | 
The serializer to use for serializing and deserializing checkpoints. Defaults to None.



 | `None` |

Examples:

```
    import asyncio

    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import StateGraph

    builder = StateGraph(int)
    builder.add_node("add_one", lambda x: x + 1)
    builder.set_entry_point("add_one")
    builder.set_finish_point("add_one")

    memory = InMemorySaver()
    graph = builder.compile(checkpointer=memory)
    coro = graph.ainvoke(1, {"configurable": {"thread_id": "thread-1"}})
    asyncio.run(coro)  # Output: 2

```

Methods:

| Name | Description |
| --- | --- |
| `[get_tuple](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.memory.InMemorySaver.get_tuple "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">get_tuple</span> (<code>langgraph.checkpoint.memory.InMemorySaver.get_tuple</code>)")` | 
Get a checkpoint tuple from the in-memory storage.



 |
| `[list](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.memory.InMemorySaver.list "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">list</span> (<code>langgraph.checkpoint.memory.InMemorySaver.list</code>)")` | 

List checkpoints from the in-memory storage.



 |
| `[put](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.memory.InMemorySaver.put "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">put</span> (<code>langgraph.checkpoint.memory.InMemorySaver.put</code>)")` | 

Save a checkpoint to the in-memory storage.



 |
| `[put_writes](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.memory.InMemorySaver.put_writes "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">put_writes</span> (<code>langgraph.checkpoint.memory.InMemorySaver.put_writes</code>)")` | 

Save a list of writes to the in-memory storage.



 |
| `[delete_thread](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.memory.InMemorySaver.delete_thread "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">delete_thread</span> (<code>langgraph.checkpoint.memory.InMemorySaver.delete_thread</code>)")` | 

Delete all checkpoints and writes associated with a thread ID.



 |
| `[aget_tuple](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.memory.InMemorySaver.aget_tuple "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">aget_tuple</span> <span class="doc doc-labels"> <small class="doc doc-label doc-label-async"><code>async</code></small> </span> (<code>langgraph.checkpoint.memory.InMemorySaver.aget_tuple</code>)")` | 

Asynchronous version of get\_tuple.



 |
| `[alist](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.memory.InMemorySaver.alist "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">alist</span> <span class="doc doc-labels"> <small class="doc doc-label doc-label-async"><code>async</code></small> </span> (<code>langgraph.checkpoint.memory.InMemorySaver.alist</code>)")` | 

Asynchronous version of list.



 |
| `[aput](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.memory.InMemorySaver.aput "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">aput</span> <span class="doc doc-labels"> <small class="doc doc-label doc-label-async"><code>async</code></small> </span> (<code>langgraph.checkpoint.memory.InMemorySaver.aput</code>)")` | 

Asynchronous version of put.



 |
| `[aput_writes](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.memory.InMemorySaver.aput_writes "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">aput_writes</span> <span class="doc doc-labels"> <small class="doc doc-label doc-label-async"><code>async</code></small> </span> (<code>langgraph.checkpoint.memory.InMemorySaver.aput_writes</code>)")` | 

Asynchronous version of put\_writes.



 |
| `[adelete_thread](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.memory.InMemorySaver.adelete_thread "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">adelete_thread</span> <span class="doc doc-labels"> <small class="doc doc-label doc-label-async"><code>async</code></small> </span> (<code>langgraph.checkpoint.memory.InMemorySaver.adelete_thread</code>)")` | 

Delete all checkpoints and writes associated with a thread ID.



 |
| `[get](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.memory.InMemorySaver.get "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">get</span> (<code>langgraph.checkpoint.memory.InMemorySaver.get</code>)")` | 

Fetch a checkpoint using the given configuration.



 |
| `[aget](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.memory.InMemorySaver.aget "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">aget</span> <span class="doc doc-labels"> <small class="doc doc-label doc-label-async"><code>async</code></small> </span> (<code>langgraph.checkpoint.memory.InMemorySaver.aget</code>)")` | 

Asynchronously fetch a checkpoint using the given configuration.



 |

Attributes:

| Name | Type | Description |
| --- | --- | --- |
| `[config_specs](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.memory.InMemorySaver.config_specs "<code class="doc-symbol doc-symbol-heading doc-symbol-attribute"></code>            <span class="doc doc-object-name doc-attribute-name">config_specs</span> <span class="doc doc-labels"> <small class="doc doc-label doc-label-property"><code>property</code></small> </span> (<code>langgraph.checkpoint.memory.InMemorySaver.config_specs</code>)")` | `[list](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.BaseCheckpointSaver.list "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">list</span> (<code>langgraph.checkpoint.base.BaseCheckpointSaver.list</code>)")` | 
Define the configuration options for the checkpoint saver.



 |

### config\_specs `property` [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.memory.InMemorySaver.config_specs "Permanent link")

```
config_specs: list

```

Define the configuration options for the checkpoint saver.

Returns:

| Name | Type | Description |
| --- | --- | --- |
| `list` | `[list](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.BaseCheckpointSaver.list "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">list</span> (<code>langgraph.checkpoint.base.BaseCheckpointSaver.list</code>)")` | 
List of configuration field specs.



 |

### get\_tuple [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.memory.InMemorySaver.get_tuple "Permanent link")

```
get_tuple(config: RunnableConfig) -> CheckpointTuple | None

```

Get a checkpoint tuple from the in-memory storage.

This method retrieves a checkpoint tuple from the in-memory storage based on the provided config. If the config contains a "checkpoint\_id" key, the checkpoint with the matching thread ID and timestamp is retrieved. Otherwise, the latest checkpoint for the given thread ID is retrieved.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `config` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>")` | 
The config to use for retrieving the checkpoint.



 | _required_ |

Returns:

| Type | Description |
| --- | --- |
| `CheckpointTuple | None` | 
Optional\[CheckpointTuple\]: The retrieved checkpoint tuple, or None if no matching checkpoint was found.



 |

### list [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.memory.InMemorySaver.list "Permanent link")

```
list(
    config: RunnableConfig | None,
    *,
    filter: dict[str, Any] | None = None,
    before: RunnableConfig | None = None,
    limit: int | None = None
) -> Iterator[CheckpointTuple]

```

List checkpoints from the in-memory storage.

This method retrieves a list of checkpoint tuples from the in-memory storage based on the provided criteria.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `config` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>") | None` | 
Base configuration for filtering checkpoints.



 | _required_ |
| `filter` | `[dict](https://docs.python.org/3/library/stdtypes.html#dict)[[str](https://docs.python.org/3/library/stdtypes.html#str), [Any](https://docs.python.org/3/library/typing.html#typing.Any "<code>typing.Any</code>")] | None` | 

Additional filtering criteria for metadata.



 | `None` |
| `before` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>") | None` | 

List checkpoints created before this configuration.



 | `None` |
| `limit` | `[int](https://docs.python.org/3/library/functions.html#int) | None` | 

Maximum number of checkpoints to return.



 | `None` |

Yields:

| Type | Description |
| --- | --- |
| `CheckpointTuple` | 
Iterator\[CheckpointTuple\]: An iterator of matching checkpoint tuples.



 |

### put [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.memory.InMemorySaver.put "Permanent link")

```
put(
    config: RunnableConfig,
    checkpoint: Checkpoint,
    metadata: CheckpointMetadata,
    new_versions: ChannelVersions,
) -> RunnableConfig

```

Save a checkpoint to the in-memory storage.

This method saves a checkpoint to the in-memory storage. The checkpoint is associated with the provided config.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `config` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>")` | 
The config to associate with the checkpoint.



 | _required_ |
| `checkpoint` | `[Checkpoint](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.Checkpoint "<code class="doc-symbol doc-symbol-heading doc-symbol-class"></code>            <span class="doc doc-object-name doc-class-name">Checkpoint</span> (<code>langgraph.checkpoint.base.Checkpoint</code>)")` | 

The checkpoint to save.



 | _required_ |
| `metadata` | `[CheckpointMetadata](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.CheckpointMetadata "<code class="doc-symbol doc-symbol-heading doc-symbol-class"></code>            <span class="doc doc-object-name doc-class-name">CheckpointMetadata</span> (<code>langgraph.checkpoint.base.CheckpointMetadata</code>)")` | 

Additional metadata to save with the checkpoint.



 | _required_ |
| `new_versions` | `ChannelVersions` | 

New versions as of this write



 | _required_ |

Returns:

| Name | Type | Description |
| --- | --- | --- |
| `RunnableConfig` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>")` | 
The updated config containing the saved checkpoint's timestamp.



 |

### put\_writes [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.memory.InMemorySaver.put_writes "Permanent link")

```
put_writes(
    config: RunnableConfig,
    writes: Sequence[tuple[str, Any]],
    task_id: str,
    task_path: str = "",
) -> None

```

Save a list of writes to the in-memory storage.

This method saves a list of writes to the in-memory storage. The writes are associated with the provided config.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `config` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>")` | 
The config to associate with the writes.



 | _required_ |
| `writes` | `[Sequence](https://docs.python.org/3/library/collections.abc.html#collections.abc.Sequence "<code>collections.abc.Sequence</code>")[[tuple](https://docs.python.org/3/library/stdtypes.html#tuple)[[str](https://docs.python.org/3/library/stdtypes.html#str), [Any](https://docs.python.org/3/library/typing.html#typing.Any "<code>typing.Any</code>")]]` | 

The writes to save.



 | _required_ |
| `task_id` | `[str](https://docs.python.org/3/library/stdtypes.html#str)` | 

Identifier for the task creating the writes.



 | _required_ |
| `task_path` | `[str](https://docs.python.org/3/library/stdtypes.html#str)` | 

Path of the task creating the writes.



 | `''` |

Returns:

| Name | Type | Description |
| --- | --- | --- |
| `RunnableConfig` | `None` | 
The updated config containing the saved writes' timestamp.



 |

### delete\_thread [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.memory.InMemorySaver.delete_thread "Permanent link")

```
delete_thread(thread_id: str) -> None

```

Delete all checkpoints and writes associated with a thread ID.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `thread_id` | `[str](https://docs.python.org/3/library/stdtypes.html#str)` | 
The thread ID to delete.



 | _required_ |

Returns:

| Type | Description |
| --- | --- |
| `None` | 
None



 |

### aget\_tuple `async` [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.memory.InMemorySaver.aget_tuple "Permanent link")

```
aget_tuple(
    config: RunnableConfig,
) -> CheckpointTuple | None

```

Asynchronous version of get\_tuple.

This method is an asynchronous wrapper around get\_tuple that runs the synchronous method in a separate thread using asyncio.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `config` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>")` | 
The config to use for retrieving the checkpoint.



 | _required_ |

Returns:

| Type | Description |
| --- | --- |
| `CheckpointTuple | None` | 
Optional\[CheckpointTuple\]: The retrieved checkpoint tuple, or None if no matching checkpoint was found.



 |

### alist `async` [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.memory.InMemorySaver.alist "Permanent link")

```
alist(
    config: RunnableConfig | None,
    *,
    filter: dict[str, Any] | None = None,
    before: RunnableConfig | None = None,
    limit: int | None = None
) -> AsyncIterator[CheckpointTuple]

```

Asynchronous version of list.

This method is an asynchronous wrapper around list that runs the synchronous method in a separate thread using asyncio.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `config` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>") | None` | 
The config to use for listing the checkpoints.



 | _required_ |

Yields:

| Type | Description |
| --- | --- |
| `[AsyncIterator](https://docs.python.org/3/library/collections.abc.html#collections.abc.AsyncIterator "<code>collections.abc.AsyncIterator</code>")[CheckpointTuple]` | 
AsyncIterator\[CheckpointTuple\]: An asynchronous iterator of checkpoint tuples.



 |

### aput `async` [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.memory.InMemorySaver.aput "Permanent link")

```
aput(
    config: RunnableConfig,
    checkpoint: Checkpoint,
    metadata: CheckpointMetadata,
    new_versions: ChannelVersions,
) -> RunnableConfig

```

Asynchronous version of put.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `config` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>")` | 
The config to associate with the checkpoint.



 | _required_ |
| `checkpoint` | `[Checkpoint](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.Checkpoint "<code class="doc-symbol doc-symbol-heading doc-symbol-class"></code>            <span class="doc doc-object-name doc-class-name">Checkpoint</span> (<code>langgraph.checkpoint.base.Checkpoint</code>)")` | 

The checkpoint to save.



 | _required_ |
| `metadata` | `[CheckpointMetadata](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.CheckpointMetadata "<code class="doc-symbol doc-symbol-heading doc-symbol-class"></code>            <span class="doc doc-object-name doc-class-name">CheckpointMetadata</span> (<code>langgraph.checkpoint.base.CheckpointMetadata</code>)")` | 

Additional metadata to save with the checkpoint.



 | _required_ |
| `new_versions` | `ChannelVersions` | 

New versions as of this write



 | _required_ |

Returns:

| Name | Type | Description |
| --- | --- | --- |
| `RunnableConfig` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>")` | 
The updated config containing the saved checkpoint's timestamp.



 |

### aput\_writes `async` [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.memory.InMemorySaver.aput_writes "Permanent link")

```
aput_writes(
    config: RunnableConfig,
    writes: Sequence[tuple[str, Any]],
    task_id: str,
    task_path: str = "",
) -> None

```

Asynchronous version of put\_writes.

This method is an asynchronous wrapper around put\_writes that runs the synchronous method in a separate thread using asyncio.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `config` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>")` | 
The config to associate with the writes.



 | _required_ |
| `writes` | `[Sequence](https://docs.python.org/3/library/collections.abc.html#collections.abc.Sequence "<code>collections.abc.Sequence</code>")[[tuple](https://docs.python.org/3/library/stdtypes.html#tuple)[[str](https://docs.python.org/3/library/stdtypes.html#str), [Any](https://docs.python.org/3/library/typing.html#typing.Any "<code>typing.Any</code>")]]` | 

The writes to save, each as a (channel, value) pair.



 | _required_ |
| `task_id` | `[str](https://docs.python.org/3/library/stdtypes.html#str)` | 

Identifier for the task creating the writes.



 | _required_ |
| `task_path` | `[str](https://docs.python.org/3/library/stdtypes.html#str)` | 

Path of the task creating the writes.



 | `''` |

Returns:

| Type | Description |
| --- | --- |
| `None` | 
None



 |

### adelete\_thread `async` [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.memory.InMemorySaver.adelete_thread "Permanent link")

```
adelete_thread(thread_id: str) -> None

```

Delete all checkpoints and writes associated with a thread ID.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `thread_id` | `[str](https://docs.python.org/3/library/stdtypes.html#str)` | 
The thread ID to delete.



 | _required_ |

Returns:

| Type | Description |
| --- | --- |
| `None` | 
None



 |

### get [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.memory.InMemorySaver.get "Permanent link")

```
get(config: RunnableConfig) -> Checkpoint | None

```

Fetch a checkpoint using the given configuration.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `config` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>")` | 
Configuration specifying which checkpoint to retrieve.



 | _required_ |

Returns:

| Type | Description |
| --- | --- |
| `[Checkpoint](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.Checkpoint "<code class="doc-symbol doc-symbol-heading doc-symbol-class"></code>            <span class="doc doc-object-name doc-class-name">Checkpoint</span> (<code>langgraph.checkpoint.base.Checkpoint</code>)") | None` | 
Optional\[Checkpoint\]: The requested checkpoint, or None if not found.



 |

### aget `async` [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.memory.InMemorySaver.aget "Permanent link")

```
aget(config: RunnableConfig) -> Checkpoint | None

```

Asynchronously fetch a checkpoint using the given configuration.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `config` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>")` | 
Configuration specifying which checkpoint to retrieve.



 | _required_ |

Returns:

| Type | Description |
| --- | --- |
| `[Checkpoint](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.Checkpoint "<code class="doc-symbol doc-symbol-heading doc-symbol-class"></code>            <span class="doc doc-object-name doc-class-name">Checkpoint</span> (<code>langgraph.checkpoint.base.Checkpoint</code>)") | None` | 
Optional\[Checkpoint\]: The requested checkpoint, or None if not found.



 |

## PersistentDict [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.memory.PersistentDict "Permanent link")

Bases: `[defaultdict](https://docs.python.org/3/library/collections.html#collections.defaultdict "<code>collections.defaultdict</code>")`

Persistent dictionary with an API compatible with shelve and anydbm.

The dict is kept in memory, so the dictionary operations run as fast as a regular dictionary.

Write to disk is delayed until close or sync (similar to gdbm's fast mode).

Input file format is automatically discovered. Output file format is selectable between pickle, json, and csv. All three serialization formats are backed by fast C implementations.

Adapted from [https://code.activestate.com/recipes/576642-persistent-dict-with-multiple-standard-file-format/](https://code.activestate.com/recipes/576642-persistent-dict-with-multiple-standard-file-format/)

Methods:

| Name | Description |
| --- | --- |
| `[sync](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.memory.PersistentDict.sync "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">sync</span> (<code>langgraph.checkpoint.memory.PersistentDict.sync</code>)")` | 
Write dict to disk



 |

### sync [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.memory.PersistentDict.sync "Permanent link")

```
sync() -> None

```

Write dict to disk

Modules:

| Name | Description |
| --- | --- |
| `aio` | 
 |
| `utils` | 

 |

Classes:

| Name | Description |
| --- | --- |
| `[SqliteSaver](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.SqliteSaver "<code class="doc-symbol doc-symbol-heading doc-symbol-class"></code>            <span class="doc doc-object-name doc-class-name">SqliteSaver</span> (<code>langgraph.checkpoint.sqlite.SqliteSaver</code>)")` | 
A checkpoint saver that stores checkpoints in a SQLite database.



 |

## SqliteSaver [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.SqliteSaver "Permanent link")

Bases: `[BaseCheckpointSaver](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.BaseCheckpointSaver "<code class="doc-symbol doc-symbol-heading doc-symbol-class"></code>            <span class="doc doc-object-name doc-class-name">BaseCheckpointSaver</span> (<code>langgraph.checkpoint.base.BaseCheckpointSaver</code>)")[[str](https://docs.python.org/3/library/stdtypes.html#str)]`

A checkpoint saver that stores checkpoints in a SQLite database.

Note

This class is meant for lightweight, synchronous use cases (demos and small projects) and does not scale to multiple threads. For a similar sqlite saver with `async` support, consider using [AsyncSqliteSaver](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver "<code class="doc-symbol doc-symbol-heading doc-symbol-class"></code>            <span class="doc doc-object-name doc-class-name">AsyncSqliteSaver</span>").

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `conn` | `[Connection](https://docs.python.org/3/library/sqlite3.html#sqlite3.Connection "<code>sqlite3.Connection</code>")` | 
The SQLite database connection.



 | _required_ |
| `serde` | `Optional[[SerializerProtocol](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.serde.base.SerializerProtocol "<code class="doc-symbol doc-symbol-heading doc-symbol-class"></code>            <span class="doc doc-object-name doc-class-name">SerializerProtocol</span> (<code>langgraph.checkpoint.base.SerializerProtocol</code>)")]` | 

The serializer to use for serializing and deserializing checkpoints. Defaults to JsonPlusSerializerCompat.



 | `None` |

Examples:

```
>>> import sqlite3
>>> from langgraph.checkpoint.sqlite import SqliteSaver
>>> from langgraph.graph import StateGraph
>>>
>>> builder = StateGraph(int)
>>> builder.add_node("add_one", lambda x: x + 1)
>>> builder.set_entry_point("add_one")
>>> builder.set_finish_point("add_one")
>>> # Create a new SqliteSaver instance
>>> # Note: check_same_thread=False is OK as the implementation uses a lock
>>> # to ensure thread safety.
>>> conn = sqlite3.connect("checkpoints.sqlite", check_same_thread=False)
>>> memory = SqliteSaver(conn)
>>> graph = builder.compile(checkpointer=memory)
>>> config = {"configurable": {"thread_id": "1"}}
>>> graph.get_state(config)
>>> result = graph.invoke(3, config)
>>> graph.get_state(config)
StateSnapshot(values=4, next=(), config={'configurable': {'thread_id': '1', 'checkpoint_ns': '', 'checkpoint_id': '0c62ca34-ac19-445d-bbb0-5b4984975b2a'}}, parent_config=None)

```

Methods:

| Name | Description |
| --- | --- |
| `[from_conn_string](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.SqliteSaver.from_conn_string "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">from_conn_string</span> <span class="doc doc-labels"> <small class="doc doc-label doc-label-classmethod"><code>classmethod</code></small> </span> (<code>langgraph.checkpoint.sqlite.SqliteSaver.from_conn_string</code>)")` | 
Create a new SqliteSaver instance from a connection string.



 |
| `[setup](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.SqliteSaver.setup "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">setup</span> (<code>langgraph.checkpoint.sqlite.SqliteSaver.setup</code>)")` | 

Set up the checkpoint database.



 |
| `[cursor](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.SqliteSaver.cursor "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">cursor</span> (<code>langgraph.checkpoint.sqlite.SqliteSaver.cursor</code>)")` | 

Get a cursor for the SQLite database.



 |
| `[get_tuple](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.SqliteSaver.get_tuple "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">get_tuple</span> (<code>langgraph.checkpoint.sqlite.SqliteSaver.get_tuple</code>)")` | 

Get a checkpoint tuple from the database.



 |
| `[list](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.SqliteSaver.list "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">list</span> (<code>langgraph.checkpoint.sqlite.SqliteSaver.list</code>)")` | 

List checkpoints from the database.



 |
| `[put](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.SqliteSaver.put "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">put</span> (<code>langgraph.checkpoint.sqlite.SqliteSaver.put</code>)")` | 

Save a checkpoint to the database.



 |
| `[put_writes](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.SqliteSaver.put_writes "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">put_writes</span> (<code>langgraph.checkpoint.sqlite.SqliteSaver.put_writes</code>)")` | 

Store intermediate writes linked to a checkpoint.



 |
| `[delete_thread](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.SqliteSaver.delete_thread "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">delete_thread</span> (<code>langgraph.checkpoint.sqlite.SqliteSaver.delete_thread</code>)")` | 

Delete all checkpoints and writes associated with a thread ID.



 |
| `[aget_tuple](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.SqliteSaver.aget_tuple "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">aget_tuple</span> <span class="doc doc-labels"> <small class="doc doc-label doc-label-async"><code>async</code></small> </span> (<code>langgraph.checkpoint.sqlite.SqliteSaver.aget_tuple</code>)")` | 

Get a checkpoint tuple from the database asynchronously.



 |
| `[alist](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.SqliteSaver.alist "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">alist</span> <span class="doc doc-labels"> <small class="doc doc-label doc-label-async"><code>async</code></small> </span> (<code>langgraph.checkpoint.sqlite.SqliteSaver.alist</code>)")` | 

List checkpoints from the database asynchronously.



 |
| `[aput](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.SqliteSaver.aput "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">aput</span> <span class="doc doc-labels"> <small class="doc doc-label doc-label-async"><code>async</code></small> </span> (<code>langgraph.checkpoint.sqlite.SqliteSaver.aput</code>)")` | 

Save a checkpoint to the database asynchronously.



 |
| `[get_next_version](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.SqliteSaver.get_next_version "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">get_next_version</span> (<code>langgraph.checkpoint.sqlite.SqliteSaver.get_next_version</code>)")` | 

Generate the next version ID for a channel.



 |
| `[get](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.SqliteSaver.get "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">get</span> (<code>langgraph.checkpoint.sqlite.SqliteSaver.get</code>)")` | 

Fetch a checkpoint using the given configuration.



 |
| `[aget](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.SqliteSaver.aget "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">aget</span> <span class="doc doc-labels"> <small class="doc doc-label doc-label-async"><code>async</code></small> </span> (<code>langgraph.checkpoint.sqlite.SqliteSaver.aget</code>)")` | 

Asynchronously fetch a checkpoint using the given configuration.



 |
| `[aput_writes](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.SqliteSaver.aput_writes "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">aput_writes</span> <span class="doc doc-labels"> <small class="doc doc-label doc-label-async"><code>async</code></small> </span> (<code>langgraph.checkpoint.sqlite.SqliteSaver.aput_writes</code>)")` | 

Asynchronously store intermediate writes linked to a checkpoint.



 |
| `[adelete_thread](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.SqliteSaver.adelete_thread "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">adelete_thread</span> <span class="doc doc-labels"> <small class="doc doc-label doc-label-async"><code>async</code></small> </span> (<code>langgraph.checkpoint.sqlite.SqliteSaver.adelete_thread</code>)")` | 

Delete all checkpoints and writes associated with a specific thread ID.



 |

Attributes:

| Name | Type | Description |
| --- | --- | --- |
| `[config_specs](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.SqliteSaver.config_specs "<code class="doc-symbol doc-symbol-heading doc-symbol-attribute"></code>            <span class="doc doc-object-name doc-attribute-name">config_specs</span> <span class="doc doc-labels"> <small class="doc doc-label doc-label-property"><code>property</code></small> </span> (<code>langgraph.checkpoint.sqlite.SqliteSaver.config_specs</code>)")` | `[list](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.BaseCheckpointSaver.list "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">list</span> (<code>langgraph.checkpoint.base.BaseCheckpointSaver.list</code>)")` | 
Define the configuration options for the checkpoint saver.



 |

### config\_specs `property` [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.SqliteSaver.config_specs "Permanent link")

```
config_specs: list

```

Define the configuration options for the checkpoint saver.

Returns:

| Name | Type | Description |
| --- | --- | --- |
| `list` | `[list](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.BaseCheckpointSaver.list "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">list</span> (<code>langgraph.checkpoint.base.BaseCheckpointSaver.list</code>)")` | 
List of configuration field specs.



 |

### from\_conn\_string `classmethod` [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.SqliteSaver.from_conn_string "Permanent link")

```
from_conn_string(conn_string: str) -> Iterator[SqliteSaver]

```

Create a new SqliteSaver instance from a connection string.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `conn_string` | `[str](https://docs.python.org/3/library/stdtypes.html#str)` | 
The SQLite connection string.



 | _required_ |

Yields:

| Name | Type | Description |
| --- | --- | --- |
| `SqliteSaver` | `[SqliteSaver](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.SqliteSaver "<code class="doc-symbol doc-symbol-heading doc-symbol-class"></code>            <span class="doc doc-object-name doc-class-name">SqliteSaver</span> (<code>langgraph.checkpoint.sqlite.SqliteSaver</code>)")` | 
A new SqliteSaver instance.



 |

Examples:

```
In memory:

    with SqliteSaver.from_conn_string(":memory:") as memory:
        ...

To disk:

    with SqliteSaver.from_conn_string("checkpoints.sqlite") as memory:
        ...

```

### setup [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.SqliteSaver.setup "Permanent link")

```
setup() -> None

```

Set up the checkpoint database.

This method creates the necessary tables in the SQLite database if they don't already exist. It is called automatically when needed and should not be called directly by the user.

### cursor [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.SqliteSaver.cursor "Permanent link")

```
cursor(transaction: bool = True) -> Iterator[Cursor]

```

Get a cursor for the SQLite database.

This method returns a cursor for the SQLite database. It is used internally by the SqliteSaver and should not be called directly by the user.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `transaction` | `[bool](https://docs.python.org/3/library/functions.html#bool)` | 
Whether to commit the transaction when the cursor is closed. Defaults to True.



 | `True` |

Yields:

| Type | Description |
| --- | --- |
| `[Cursor](https://docs.python.org/3/library/sqlite3.html#sqlite3.Cursor "<code>sqlite3.Cursor</code>")` | 
sqlite3.Cursor: A cursor for the SQLite database.



 |

### get\_tuple [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.SqliteSaver.get_tuple "Permanent link")

```
get_tuple(config: RunnableConfig) -> CheckpointTuple | None

```

Get a checkpoint tuple from the database.

This method retrieves a checkpoint tuple from the SQLite database based on the provided config. If the config contains a "checkpoint\_id" key, the checkpoint with the matching thread ID and checkpoint ID is retrieved. Otherwise, the latest checkpoint for the given thread ID is retrieved.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `config` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>")` | 
The config to use for retrieving the checkpoint.



 | _required_ |

Returns:

| Type | Description |
| --- | --- |
| `CheckpointTuple | None` | 
Optional\[CheckpointTuple\]: The retrieved checkpoint tuple, or None if no matching checkpoint was found.



 |

Examples:

```
Basic:
>>> config = {"configurable": {"thread_id": "1"}}
>>> checkpoint_tuple = memory.get_tuple(config)
>>> print(checkpoint_tuple)
CheckpointTuple(...)

With checkpoint ID:

>>> config = {
...    "configurable": {
...        "thread_id": "1",
...        "checkpoint_ns": "",
...        "checkpoint_id": "1ef4f797-8335-6428-8001-8a1503f9b875",
...    }
... }
>>> checkpoint_tuple = memory.get_tuple(config)
>>> print(checkpoint_tuple)
CheckpointTuple(...)

```

### list [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.SqliteSaver.list "Permanent link")

```
list(
    config: RunnableConfig | None,
    *,
    filter: dict[str, Any] | None = None,
    before: RunnableConfig | None = None,
    limit: int | None = None
) -> Iterator[CheckpointTuple]

```

List checkpoints from the database.

This method retrieves a list of checkpoint tuples from the SQLite database based on the provided config. The checkpoints are ordered by checkpoint ID in descending order (newest first).

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `config` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>") | None` | 
The config to use for listing the checkpoints.



 | _required_ |
| `filter` | `[dict](https://docs.python.org/3/library/stdtypes.html#dict)[[str](https://docs.python.org/3/library/stdtypes.html#str), [Any](https://docs.python.org/3/library/typing.html#typing.Any "<code>typing.Any</code>")] | None` | 

Additional filtering criteria for metadata. Defaults to None.



 | `None` |
| `before` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>") | None` | 

If provided, only checkpoints before the specified checkpoint ID are returned. Defaults to None.



 | `None` |
| `limit` | `[int](https://docs.python.org/3/library/functions.html#int) | None` | 

The maximum number of checkpoints to return. Defaults to None.



 | `None` |

Yields:

| Type | Description |
| --- | --- |
| `CheckpointTuple` | 
Iterator\[CheckpointTuple\]: An iterator of checkpoint tuples.



 |

Examples:

```
>>> from langgraph.checkpoint.sqlite import SqliteSaver
>>> with SqliteSaver.from_conn_string(":memory:") as memory:
... # Run a graph, then list the checkpoints
>>>     config = {"configurable": {"thread_id": "1"}}
>>>     checkpoints = list(memory.list(config, limit=2))
>>> print(checkpoints)
[CheckpointTuple(...), CheckpointTuple(...)]

```

```
>>> config = {"configurable": {"thread_id": "1"}}
>>> before = {"configurable": {"checkpoint_id": "1ef4f797-8335-6428-8001-8a1503f9b875"}}
>>> with SqliteSaver.from_conn_string(":memory:") as memory:
... # Run a graph, then list the checkpoints
>>>     checkpoints = list(memory.list(config, before=before))
>>> print(checkpoints)
[CheckpointTuple(...), ...]

```

### put [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.SqliteSaver.put "Permanent link")

```
put(
    config: RunnableConfig,
    checkpoint: Checkpoint,
    metadata: CheckpointMetadata,
    new_versions: ChannelVersions,
) -> RunnableConfig

```

Save a checkpoint to the database.

This method saves a checkpoint to the SQLite database. The checkpoint is associated with the provided config and its parent config (if any).

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `config` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>")` | 
The config to associate with the checkpoint.



 | _required_ |
| `checkpoint` | `[Checkpoint](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.Checkpoint "<code class="doc-symbol doc-symbol-heading doc-symbol-class"></code>            <span class="doc doc-object-name doc-class-name">Checkpoint</span> (<code>langgraph.checkpoint.base.Checkpoint</code>)")` | 

The checkpoint to save.



 | _required_ |
| `metadata` | `[CheckpointMetadata](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.CheckpointMetadata "<code class="doc-symbol doc-symbol-heading doc-symbol-class"></code>            <span class="doc doc-object-name doc-class-name">CheckpointMetadata</span> (<code>langgraph.checkpoint.base.CheckpointMetadata</code>)")` | 

Additional metadata to save with the checkpoint.



 | _required_ |
| `new_versions` | `ChannelVersions` | 

New channel versions as of this write.



 | _required_ |

Returns:

| Name | Type | Description |
| --- | --- | --- |
| `RunnableConfig` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>")` | 
Updated configuration after storing the checkpoint.



 |

Examples:

```
>>> from langgraph.checkpoint.sqlite import SqliteSaver
>>> with SqliteSaver.from_conn_string(":memory:") as memory:
>>>     config = {"configurable": {"thread_id": "1", "checkpoint_ns": ""}}
>>>     checkpoint = {"ts": "2024-05-04T06:32:42.235444+00:00", "id": "1ef4f797-8335-6428-8001-8a1503f9b875", "channel_values": {"key": "value"}}
>>>     saved_config = memory.put(config, checkpoint, {"source": "input", "step": 1, "writes": {"key": "value"}}, {})
>>> print(saved_config)
{'configurable': {'thread_id': '1', 'checkpoint_ns': '', 'checkpoint_id': '1ef4f797-8335-6428-8001-8a1503f9b875'}}

```

### put\_writes [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.SqliteSaver.put_writes "Permanent link")

```
put_writes(
    config: RunnableConfig,
    writes: Sequence[tuple[str, Any]],
    task_id: str,
    task_path: str = "",
) -> None

```

Store intermediate writes linked to a checkpoint.

This method saves intermediate writes associated with a checkpoint to the SQLite database.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `config` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>")` | 
Configuration of the related checkpoint.



 | _required_ |
| `writes` | `[Sequence](https://docs.python.org/3/library/collections.abc.html#collections.abc.Sequence "<code>collections.abc.Sequence</code>")[[tuple](https://docs.python.org/3/library/stdtypes.html#tuple)[[str](https://docs.python.org/3/library/stdtypes.html#str), [Any](https://docs.python.org/3/library/typing.html#typing.Any "<code>typing.Any</code>")]]` | 

List of writes to store, each as (channel, value) pair.



 | _required_ |
| `task_id` | `[str](https://docs.python.org/3/library/stdtypes.html#str)` | 

Identifier for the task creating the writes.



 | _required_ |
| `task_path` | `[str](https://docs.python.org/3/library/stdtypes.html#str)` | 

Path of the task creating the writes.



 | `''` |

### delete\_thread [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.SqliteSaver.delete_thread "Permanent link")

```
delete_thread(thread_id: str) -> None

```

Delete all checkpoints and writes associated with a thread ID.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `thread_id` | `[str](https://docs.python.org/3/library/stdtypes.html#str)` | 
The thread ID to delete.



 | _required_ |

Returns:

| Type | Description |
| --- | --- |
| `None` | 
None



 |

### aget\_tuple `async` [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.SqliteSaver.aget_tuple "Permanent link")

```
aget_tuple(
    config: RunnableConfig,
) -> CheckpointTuple | None

```

Get a checkpoint tuple from the database asynchronously.

Note

This async method is not supported by the SqliteSaver class. Use get\_tuple() instead, or consider using [AsyncSqliteSaver](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver "<code class="doc-symbol doc-symbol-heading doc-symbol-class"></code>            <span class="doc doc-object-name doc-class-name">AsyncSqliteSaver</span>").

### alist `async` [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.SqliteSaver.alist "Permanent link")

```
alist(
    config: RunnableConfig | None,
    *,
    filter: dict[str, Any] | None = None,
    before: RunnableConfig | None = None,
    limit: int | None = None
) -> AsyncIterator[CheckpointTuple]

```

List checkpoints from the database asynchronously.

Note

This async method is not supported by the SqliteSaver class. Use list() instead, or consider using [AsyncSqliteSaver](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver "<code class="doc-symbol doc-symbol-heading doc-symbol-class"></code>            <span class="doc doc-object-name doc-class-name">AsyncSqliteSaver</span>").

### aput `async` [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.SqliteSaver.aput "Permanent link")

```
aput(
    config: RunnableConfig,
    checkpoint: Checkpoint,
    metadata: CheckpointMetadata,
    new_versions: ChannelVersions,
) -> RunnableConfig

```

Save a checkpoint to the database asynchronously.

Note

This async method is not supported by the SqliteSaver class. Use put() instead, or consider using [AsyncSqliteSaver](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver "<code class="doc-symbol doc-symbol-heading doc-symbol-class"></code>            <span class="doc doc-object-name doc-class-name">AsyncSqliteSaver</span>").

### get\_next\_version [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.SqliteSaver.get_next_version "Permanent link")

```
get_next_version(current: str | None, channel: None) -> str

```

Generate the next version ID for a channel.

This method creates a new version identifier for a channel based on its current version.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `current` | `Optional[[str](https://docs.python.org/3/library/stdtypes.html#str)]` | 
The current version identifier of the channel.



 | _required_ |

Returns:

| Name | Type | Description |
| --- | --- | --- |
| `str` | `[str](https://docs.python.org/3/library/stdtypes.html#str)` | 
The next version identifier, which is guaranteed to be monotonically increasing.



 |

### get [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.SqliteSaver.get "Permanent link")

```
get(config: RunnableConfig) -> Checkpoint | None

```

Fetch a checkpoint using the given configuration.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `config` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>")` | 
Configuration specifying which checkpoint to retrieve.



 | _required_ |

Returns:

| Type | Description |
| --- | --- |
| `[Checkpoint](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.Checkpoint "<code class="doc-symbol doc-symbol-heading doc-symbol-class"></code>            <span class="doc doc-object-name doc-class-name">Checkpoint</span> (<code>langgraph.checkpoint.base.Checkpoint</code>)") | None` | 
Optional\[Checkpoint\]: The requested checkpoint, or None if not found.



 |

### aget `async` [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.SqliteSaver.aget "Permanent link")

```
aget(config: RunnableConfig) -> Checkpoint | None

```

Asynchronously fetch a checkpoint using the given configuration.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `config` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>")` | 
Configuration specifying which checkpoint to retrieve.



 | _required_ |

Returns:

| Type | Description |
| --- | --- |
| `[Checkpoint](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.Checkpoint "<code class="doc-symbol doc-symbol-heading doc-symbol-class"></code>            <span class="doc doc-object-name doc-class-name">Checkpoint</span> (<code>langgraph.checkpoint.base.Checkpoint</code>)") | None` | 
Optional\[Checkpoint\]: The requested checkpoint, or None if not found.



 |

### aput\_writes `async` [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.SqliteSaver.aput_writes "Permanent link")

```
aput_writes(
    config: RunnableConfig,
    writes: Sequence[tuple[str, Any]],
    task_id: str,
    task_path: str = "",
) -> None

```

Asynchronously store intermediate writes linked to a checkpoint.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `config` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>")` | 
Configuration of the related checkpoint.



 | _required_ |
| `writes` | `[Sequence](https://docs.python.org/3/library/collections.abc.html#collections.abc.Sequence "<code>collections.abc.Sequence</code>")[[tuple](https://docs.python.org/3/library/stdtypes.html#tuple)[[str](https://docs.python.org/3/library/stdtypes.html#str), [Any](https://docs.python.org/3/library/typing.html#typing.Any "<code>typing.Any</code>")]]` | 

List of writes to store.



 | _required_ |
| `task_id` | `[str](https://docs.python.org/3/library/stdtypes.html#str)` | 

Identifier for the task creating the writes.



 | _required_ |
| `task_path` | `[str](https://docs.python.org/3/library/stdtypes.html#str)` | 

Path of the task creating the writes.



 | `''` |

Raises:

| Type | Description |
| --- | --- |
| `[NotImplementedError](https://docs.python.org/3/library/exceptions.html#NotImplementedError)` | 
Implement this method in your custom checkpoint saver.



 |

### adelete\_thread `async` [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.SqliteSaver.adelete_thread "Permanent link")

```
adelete_thread(thread_id: str) -> None

```

Delete all checkpoints and writes associated with a specific thread ID.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `thread_id` | `[str](https://docs.python.org/3/library/stdtypes.html#str)` | 
The thread ID whose checkpoints should be deleted.



 | _required_ |

Classes:

| Name | Description |
| --- | --- |
| `[AsyncSqliteSaver](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver "<code class="doc-symbol doc-symbol-heading doc-symbol-class"></code>            <span class="doc doc-object-name doc-class-name">AsyncSqliteSaver</span> (<code>langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver</code>)")` | 
An asynchronous checkpoint saver that stores checkpoints in a SQLite database.



 |

## AsyncSqliteSaver [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver "Permanent link")

Bases: `[BaseCheckpointSaver](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.BaseCheckpointSaver "<code class="doc-symbol doc-symbol-heading doc-symbol-class"></code>            <span class="doc doc-object-name doc-class-name">BaseCheckpointSaver</span> (<code>langgraph.checkpoint.base.BaseCheckpointSaver</code>)")[[str](https://docs.python.org/3/library/stdtypes.html#str)]`

An asynchronous checkpoint saver that stores checkpoints in a SQLite database.

This class provides an asynchronous interface for saving and retrieving checkpoints using a SQLite database. It's designed for use in asynchronous environments and offers better performance for I/O-bound operations compared to synchronous alternatives.

Attributes:

| Name | Type | Description |
| --- | --- | --- |
| `conn` | `Connection` | 
The asynchronous SQLite database connection.



 |
| `serde` | `[SerializerProtocol](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.serde.base.SerializerProtocol "<code class="doc-symbol doc-symbol-heading doc-symbol-class"></code>            <span class="doc doc-object-name doc-class-name">SerializerProtocol</span> (<code>langgraph.checkpoint.base.SerializerProtocol</code>)")` | 

The serializer used for encoding/decoding checkpoints.



 |

Tip

Requires the [aiosqlite](https://pypi.org/project/aiosqlite/) package. Install it with `pip install aiosqlite`.

Warning

While this class supports asynchronous checkpointing, it is not recommended for production workloads due to limitations in SQLite's write performance. For production use, consider a more robust database like PostgreSQL.

Tip

Remember to **close the database connection** after executing your code, otherwise, you may see the graph "hang" after execution (since the program will not exit until the connection is closed).

The easiest way is to use the `async with` statement as shown in the examples.

```
async with AsyncSqliteSaver.from_conn_string("checkpoints.sqlite") as saver:
    # Your code here
    graph = builder.compile(checkpointer=saver)
    config = {"configurable": {"thread_id": "thread-1"}}
    async for event in graph.astream_events(..., config, version="v1"):
        print(event)

```

Examples:

Usage within StateGraph:

```
>>> import asyncio
>>>
>>> from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
>>> from langgraph.graph import StateGraph
>>>
>>> async def main():
>>>     builder = StateGraph(int)
>>>     builder.add_node("add_one", lambda x: x + 1)
>>>     builder.set_entry_point("add_one")
>>>     builder.set_finish_point("add_one")
>>>     async with AsyncSqliteSaver.from_conn_string("checkpoints.db") as memory:
>>>         graph = builder.compile(checkpointer=memory)
>>>         coro = graph.ainvoke(1, {"configurable": {"thread_id": "thread-1"}})
>>>         print(await asyncio.gather(coro))
>>>
>>> asyncio.run(main())
Output: [2]

```

Raw usage:

```
>>> import asyncio
>>> import aiosqlite
>>> from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
>>>
>>> async def main():
>>>     async with aiosqlite.connect("checkpoints.db") as conn:
...         saver = AsyncSqliteSaver(conn)
...         config = {"configurable": {"thread_id": "1", "checkpoint_ns": ""}}
...         checkpoint = {"ts": "2023-05-03T10:00:00Z", "data": {"key": "value"}, "id": "0c62ca34-ac19-445d-bbb0-5b4984975b2a"}
...         saved_config = await saver.aput(config, checkpoint, {}, {})
...         print(saved_config)
>>> asyncio.run(main())
{'configurable': {'thread_id': '1', 'checkpoint_ns': '', 'checkpoint_id': '0c62ca34-ac19-445d-bbb0-5b4984975b2a'}}

```

Methods:

| Name | Description |
| --- | --- |
| `[from_conn_string](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver.from_conn_string "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">from_conn_string</span> <span class="doc doc-labels"> <small class="doc doc-label doc-label-async"><code>async</code></small> <small class="doc doc-label doc-label-classmethod"><code>classmethod</code></small> </span> (<code>langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver.from_conn_string</code>)")` | 
Create a new AsyncSqliteSaver instance from a connection string.



 |
| `[get_tuple](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver.get_tuple "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">get_tuple</span> (<code>langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver.get_tuple</code>)")` | 

Get a checkpoint tuple from the database.



 |
| `[list](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver.list "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">list</span> (<code>langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver.list</code>)")` | 

List checkpoints from the database asynchronously.



 |
| `[put](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver.put "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">put</span> (<code>langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver.put</code>)")` | 

Save a checkpoint to the database.



 |
| `[delete_thread](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver.delete_thread "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">delete_thread</span> (<code>langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver.delete_thread</code>)")` | 

Delete all checkpoints and writes associated with a thread ID.



 |
| `[setup](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver.setup "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">setup</span> <span class="doc doc-labels"> <small class="doc doc-label doc-label-async"><code>async</code></small> </span> (<code>langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver.setup</code>)")` | 

Set up the checkpoint database asynchronously.



 |
| `[aget_tuple](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver.aget_tuple "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">aget_tuple</span> <span class="doc doc-labels"> <small class="doc doc-label doc-label-async"><code>async</code></small> </span> (<code>langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver.aget_tuple</code>)")` | 

Get a checkpoint tuple from the database asynchronously.



 |
| `[alist](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver.alist "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">alist</span> <span class="doc doc-labels"> <small class="doc doc-label doc-label-async"><code>async</code></small> </span> (<code>langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver.alist</code>)")` | 

List checkpoints from the database asynchronously.



 |
| `[aput](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver.aput "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">aput</span> <span class="doc doc-labels"> <small class="doc doc-label doc-label-async"><code>async</code></small> </span> (<code>langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver.aput</code>)")` | 

Save a checkpoint to the database asynchronously.



 |
| `[aput_writes](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver.aput_writes "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">aput_writes</span> <span class="doc doc-labels"> <small class="doc doc-label doc-label-async"><code>async</code></small> </span> (<code>langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver.aput_writes</code>)")` | 

Store intermediate writes linked to a checkpoint asynchronously.



 |
| `[adelete_thread](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver.adelete_thread "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">adelete_thread</span> <span class="doc doc-labels"> <small class="doc doc-label doc-label-async"><code>async</code></small> </span> (<code>langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver.adelete_thread</code>)")` | 

Delete all checkpoints and writes associated with a thread ID.



 |
| `[get_next_version](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver.get_next_version "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">get_next_version</span> (<code>langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver.get_next_version</code>)")` | 

Generate the next version ID for a channel.



 |
| `[get](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver.get "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">get</span> (<code>langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver.get</code>)")` | 

Fetch a checkpoint using the given configuration.



 |
| `[aget](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver.aget "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">aget</span> <span class="doc doc-labels"> <small class="doc doc-label doc-label-async"><code>async</code></small> </span> (<code>langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver.aget</code>)")` | 

Asynchronously fetch a checkpoint using the given configuration.



 |

### config\_specs `property` [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver.config_specs "Permanent link")

```
config_specs: list

```

Define the configuration options for the checkpoint saver.

Returns:

| Name | Type | Description |
| --- | --- | --- |
| `list` | `[list](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.BaseCheckpointSaver.list "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">list</span> (<code>langgraph.checkpoint.base.BaseCheckpointSaver.list</code>)")` | 
List of configuration field specs.



 |

### from\_conn\_string `async` `classmethod` [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver.from_conn_string "Permanent link")

```
from_conn_string(
    conn_string: str,
) -> AsyncIterator[AsyncSqliteSaver]

```

Create a new AsyncSqliteSaver instance from a connection string.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `conn_string` | `[str](https://docs.python.org/3/library/stdtypes.html#str)` | 
The SQLite connection string.



 | _required_ |

Yields:

| Name | Type | Description |
| --- | --- | --- |
| `AsyncSqliteSaver` | `[AsyncIterator](https://docs.python.org/3/library/collections.abc.html#collections.abc.AsyncIterator "<code>collections.abc.AsyncIterator</code>")[[AsyncSqliteSaver](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver "<code class="doc-symbol doc-symbol-heading doc-symbol-class"></code>            <span class="doc doc-object-name doc-class-name">AsyncSqliteSaver</span> (<code>langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver</code>)")]` | 
A new AsyncSqliteSaver instance.



 |

### get\_tuple [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver.get_tuple "Permanent link")

```
get_tuple(config: RunnableConfig) -> CheckpointTuple | None

```

Get a checkpoint tuple from the database.

This method retrieves a checkpoint tuple from the SQLite database based on the provided config. If the config contains a "checkpoint\_id" key, the checkpoint with the matching thread ID and checkpoint ID is retrieved. Otherwise, the latest checkpoint for the given thread ID is retrieved.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `config` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>")` | 
The config to use for retrieving the checkpoint.



 | _required_ |

Returns:

| Type | Description |
| --- | --- |
| `CheckpointTuple | None` | 
Optional\[CheckpointTuple\]: The retrieved checkpoint tuple, or None if no matching checkpoint was found.



 |

### list [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver.list "Permanent link")

```
list(
    config: RunnableConfig | None,
    *,
    filter: dict[str, Any] | None = None,
    before: RunnableConfig | None = None,
    limit: int | None = None
) -> Iterator[CheckpointTuple]

```

List checkpoints from the database asynchronously.

This method retrieves a list of checkpoint tuples from the SQLite database based on the provided config. The checkpoints are ordered by checkpoint ID in descending order (newest first).

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `config` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>") | None` | 
Base configuration for filtering checkpoints.



 | _required_ |
| `filter` | `[dict](https://docs.python.org/3/library/stdtypes.html#dict)[[str](https://docs.python.org/3/library/stdtypes.html#str), [Any](https://docs.python.org/3/library/typing.html#typing.Any "<code>typing.Any</code>")] | None` | 

Additional filtering criteria for metadata.



 | `None` |
| `before` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>") | None` | 

If provided, only checkpoints before the specified checkpoint ID are returned. Defaults to None.



 | `None` |
| `limit` | `[int](https://docs.python.org/3/library/functions.html#int) | None` | 

Maximum number of checkpoints to return.



 | `None` |

Yields:

| Type | Description |
| --- | --- |
| `CheckpointTuple` | 
Iterator\[CheckpointTuple\]: An iterator of matching checkpoint tuples.



 |

### put [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver.put "Permanent link")

```
put(
    config: RunnableConfig,
    checkpoint: Checkpoint,
    metadata: CheckpointMetadata,
    new_versions: ChannelVersions,
) -> RunnableConfig

```

Save a checkpoint to the database.

This method saves a checkpoint to the SQLite database. The checkpoint is associated with the provided config and its parent config (if any).

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `config` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>")` | 
The config to associate with the checkpoint.



 | _required_ |
| `checkpoint` | `[Checkpoint](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.Checkpoint "<code class="doc-symbol doc-symbol-heading doc-symbol-class"></code>            <span class="doc doc-object-name doc-class-name">Checkpoint</span> (<code>langgraph.checkpoint.base.Checkpoint</code>)")` | 

The checkpoint to save.



 | _required_ |
| `metadata` | `[CheckpointMetadata](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.CheckpointMetadata "<code class="doc-symbol doc-symbol-heading doc-symbol-class"></code>            <span class="doc doc-object-name doc-class-name">CheckpointMetadata</span> (<code>langgraph.checkpoint.base.CheckpointMetadata</code>)")` | 

Additional metadata to save with the checkpoint.



 | _required_ |
| `new_versions` | `ChannelVersions` | 

New channel versions as of this write.



 | _required_ |

Returns:

| Name | Type | Description |
| --- | --- | --- |
| `RunnableConfig` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>")` | 
Updated configuration after storing the checkpoint.



 |

### delete\_thread [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver.delete_thread "Permanent link")

```
delete_thread(thread_id: str) -> None

```

Delete all checkpoints and writes associated with a thread ID.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `thread_id` | `[str](https://docs.python.org/3/library/stdtypes.html#str)` | 
The thread ID to delete.



 | _required_ |

Returns:

| Type | Description |
| --- | --- |
| `None` | 
None



 |

### setup `async` [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver.setup "Permanent link")

```
setup() -> None

```

Set up the checkpoint database asynchronously.

This method creates the necessary tables in the SQLite database if they don't already exist. It is called automatically when needed and should not be called directly by the user.

### aget\_tuple `async` [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver.aget_tuple "Permanent link")

```
aget_tuple(
    config: RunnableConfig,
) -> CheckpointTuple | None

```

Get a checkpoint tuple from the database asynchronously.

This method retrieves a checkpoint tuple from the SQLite database based on the provided config. If the config contains a "checkpoint\_id" key, the checkpoint with the matching thread ID and checkpoint ID is retrieved. Otherwise, the latest checkpoint for the given thread ID is retrieved.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `config` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>")` | 
The config to use for retrieving the checkpoint.



 | _required_ |

Returns:

| Type | Description |
| --- | --- |
| `CheckpointTuple | None` | 
Optional\[CheckpointTuple\]: The retrieved checkpoint tuple, or None if no matching checkpoint was found.



 |

### alist `async` [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver.alist "Permanent link")

```
alist(
    config: RunnableConfig | None,
    *,
    filter: dict[str, Any] | None = None,
    before: RunnableConfig | None = None,
    limit: int | None = None
) -> AsyncIterator[CheckpointTuple]

```

List checkpoints from the database asynchronously.

This method retrieves a list of checkpoint tuples from the SQLite database based on the provided config. The checkpoints are ordered by checkpoint ID in descending order (newest first).

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `config` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>") | None` | 
Base configuration for filtering checkpoints.



 | _required_ |
| `filter` | `[dict](https://docs.python.org/3/library/stdtypes.html#dict)[[str](https://docs.python.org/3/library/stdtypes.html#str), [Any](https://docs.python.org/3/library/typing.html#typing.Any "<code>typing.Any</code>")] | None` | 

Additional filtering criteria for metadata.



 | `None` |
| `before` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>") | None` | 

If provided, only checkpoints before the specified checkpoint ID are returned. Defaults to None.



 | `None` |
| `limit` | `[int](https://docs.python.org/3/library/functions.html#int) | None` | 

Maximum number of checkpoints to return.



 | `None` |

Yields:

| Type | Description |
| --- | --- |
| `[AsyncIterator](https://docs.python.org/3/library/collections.abc.html#collections.abc.AsyncIterator "<code>collections.abc.AsyncIterator</code>")[CheckpointTuple]` | 
AsyncIterator\[CheckpointTuple\]: An asynchronous iterator of matching checkpoint tuples.



 |

### aput `async` [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver.aput "Permanent link")

```
aput(
    config: RunnableConfig,
    checkpoint: Checkpoint,
    metadata: CheckpointMetadata,
    new_versions: ChannelVersions,
) -> RunnableConfig

```

Save a checkpoint to the database asynchronously.

This method saves a checkpoint to the SQLite database. The checkpoint is associated with the provided config and its parent config (if any).

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `config` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>")` | 
The config to associate with the checkpoint.



 | _required_ |
| `checkpoint` | `[Checkpoint](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.Checkpoint "<code class="doc-symbol doc-symbol-heading doc-symbol-class"></code>            <span class="doc doc-object-name doc-class-name">Checkpoint</span> (<code>langgraph.checkpoint.base.Checkpoint</code>)")` | 

The checkpoint to save.



 | _required_ |
| `metadata` | `[CheckpointMetadata](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.CheckpointMetadata "<code class="doc-symbol doc-symbol-heading doc-symbol-class"></code>            <span class="doc doc-object-name doc-class-name">CheckpointMetadata</span> (<code>langgraph.checkpoint.base.CheckpointMetadata</code>)")` | 

Additional metadata to save with the checkpoint.



 | _required_ |
| `new_versions` | `ChannelVersions` | 

New channel versions as of this write.



 | _required_ |

Returns:

| Name | Type | Description |
| --- | --- | --- |
| `RunnableConfig` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>")` | 
Updated configuration after storing the checkpoint.



 |

### aput\_writes `async` [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver.aput_writes "Permanent link")

```
aput_writes(
    config: RunnableConfig,
    writes: Sequence[tuple[str, Any]],
    task_id: str,
    task_path: str = "",
) -> None

```

Store intermediate writes linked to a checkpoint asynchronously.

This method saves intermediate writes associated with a checkpoint to the database.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `config` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>")` | 
Configuration of the related checkpoint.



 | _required_ |
| `writes` | `[Sequence](https://docs.python.org/3/library/collections.abc.html#collections.abc.Sequence "<code>collections.abc.Sequence</code>")[[tuple](https://docs.python.org/3/library/stdtypes.html#tuple)[[str](https://docs.python.org/3/library/stdtypes.html#str), [Any](https://docs.python.org/3/library/typing.html#typing.Any "<code>typing.Any</code>")]]` | 

List of writes to store, each as (channel, value) pair.



 | _required_ |
| `task_id` | `[str](https://docs.python.org/3/library/stdtypes.html#str)` | 

Identifier for the task creating the writes.



 | _required_ |
| `task_path` | `[str](https://docs.python.org/3/library/stdtypes.html#str)` | 

Path of the task creating the writes.



 | `''` |

### adelete\_thread `async` [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver.adelete_thread "Permanent link")

```
adelete_thread(thread_id: str) -> None

```

Delete all checkpoints and writes associated with a thread ID.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `thread_id` | `[str](https://docs.python.org/3/library/stdtypes.html#str)` | 
The thread ID to delete.



 | _required_ |

Returns:

| Type | Description |
| --- | --- |
| `None` | 
None



 |

### get\_next\_version [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver.get_next_version "Permanent link")

```
get_next_version(current: str | None, channel: None) -> str

```

Generate the next version ID for a channel.

This method creates a new version identifier for a channel based on its current version.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `current` | `Optional[[str](https://docs.python.org/3/library/stdtypes.html#str)]` | 
The current version identifier of the channel.



 | _required_ |

Returns:

| Name | Type | Description |
| --- | --- | --- |
| `str` | `[str](https://docs.python.org/3/library/stdtypes.html#str)` | 
The next version identifier, which is guaranteed to be monotonically increasing.



 |

### get [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver.get "Permanent link")

```
get(config: RunnableConfig) -> Checkpoint | None

```

Fetch a checkpoint using the given configuration.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `config` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>")` | 
Configuration specifying which checkpoint to retrieve.



 | _required_ |

Returns:

| Type | Description |
| --- | --- |
| `[Checkpoint](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.Checkpoint "<code class="doc-symbol doc-symbol-heading doc-symbol-class"></code>            <span class="doc doc-object-name doc-class-name">Checkpoint</span> (<code>langgraph.checkpoint.base.Checkpoint</code>)") | None` | 
Optional\[Checkpoint\]: The requested checkpoint, or None if not found.



 |

### aget `async` [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver.aget "Permanent link")

```
aget(config: RunnableConfig) -> Checkpoint | None

```

Asynchronously fetch a checkpoint using the given configuration.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `config` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>")` | 
Configuration specifying which checkpoint to retrieve.



 | _required_ |

Returns:

| Type | Description |
| --- | --- |
| `[Checkpoint](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.Checkpoint "<code class="doc-symbol doc-symbol-heading doc-symbol-class"></code>            <span class="doc doc-object-name doc-class-name">Checkpoint</span> (<code>langgraph.checkpoint.base.Checkpoint</code>)") | None` | 
Optional\[Checkpoint\]: The requested checkpoint, or None if not found.



 |

Classes:

| Name | Description |
| --- | --- |
| `[PostgresSaver](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.postgres.PostgresSaver "<code class="doc-symbol doc-symbol-heading doc-symbol-class"></code>            <span class="doc doc-object-name doc-class-name">PostgresSaver</span> (<code>langgraph.checkpoint.postgres.PostgresSaver</code>)")` | 
Checkpointer that stores checkpoints in a Postgres database.



 |

## PostgresSaver [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.postgres.PostgresSaver "Permanent link")

Bases: `BasePostgresSaver`

Checkpointer that stores checkpoints in a Postgres database.

Methods:

| Name | Description |
| --- | --- |
| `[from_conn_string](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.postgres.PostgresSaver.from_conn_string "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">from_conn_string</span> <span class="doc doc-labels"> <small class="doc doc-label doc-label-classmethod"><code>classmethod</code></small> </span> (<code>langgraph.checkpoint.postgres.PostgresSaver.from_conn_string</code>)")` | 
Create a new PostgresSaver instance from a connection string.



 |
| `[setup](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.postgres.PostgresSaver.setup "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">setup</span> (<code>langgraph.checkpoint.postgres.PostgresSaver.setup</code>)")` | 

Set up the checkpoint database asynchronously.



 |
| `[list](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.postgres.PostgresSaver.list "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">list</span> (<code>langgraph.checkpoint.postgres.PostgresSaver.list</code>)")` | 

List checkpoints from the database.



 |
| `[get_tuple](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.postgres.PostgresSaver.get_tuple "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">get_tuple</span> (<code>langgraph.checkpoint.postgres.PostgresSaver.get_tuple</code>)")` | 

Get a checkpoint tuple from the database.



 |
| `[put](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.postgres.PostgresSaver.put "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">put</span> (<code>langgraph.checkpoint.postgres.PostgresSaver.put</code>)")` | 

Save a checkpoint to the database.



 |
| `[put_writes](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.postgres.PostgresSaver.put_writes "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">put_writes</span> (<code>langgraph.checkpoint.postgres.PostgresSaver.put_writes</code>)")` | 

Store intermediate writes linked to a checkpoint.



 |
| `[delete_thread](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.postgres.PostgresSaver.delete_thread "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">delete_thread</span> (<code>langgraph.checkpoint.postgres.PostgresSaver.delete_thread</code>)")` | 

Delete all checkpoints and writes associated with a thread ID.



 |
| `[get](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.postgres.PostgresSaver.get "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">get</span> (<code>langgraph.checkpoint.postgres.PostgresSaver.get</code>)")` | 

Fetch a checkpoint using the given configuration.



 |
| `[aget](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.postgres.PostgresSaver.aget "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">aget</span> <span class="doc doc-labels"> <small class="doc doc-label doc-label-async"><code>async</code></small> </span> (<code>langgraph.checkpoint.postgres.PostgresSaver.aget</code>)")` | 

Asynchronously fetch a checkpoint using the given configuration.



 |
| `[aget_tuple](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.postgres.PostgresSaver.aget_tuple "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">aget_tuple</span> <span class="doc doc-labels"> <small class="doc doc-label doc-label-async"><code>async</code></small> </span> (<code>langgraph.checkpoint.postgres.PostgresSaver.aget_tuple</code>)")` | 

Asynchronously fetch a checkpoint tuple using the given configuration.



 |
| `[alist](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.postgres.PostgresSaver.alist "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">alist</span> <span class="doc doc-labels"> <small class="doc doc-label doc-label-async"><code>async</code></small> </span> (<code>langgraph.checkpoint.postgres.PostgresSaver.alist</code>)")` | 

Asynchronously list checkpoints that match the given criteria.



 |
| `[aput](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.postgres.PostgresSaver.aput "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">aput</span> <span class="doc doc-labels"> <small class="doc doc-label doc-label-async"><code>async</code></small> </span> (<code>langgraph.checkpoint.postgres.PostgresSaver.aput</code>)")` | 

Asynchronously store a checkpoint with its configuration and metadata.



 |
| `[aput_writes](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.postgres.PostgresSaver.aput_writes "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">aput_writes</span> <span class="doc doc-labels"> <small class="doc doc-label doc-label-async"><code>async</code></small> </span> (<code>langgraph.checkpoint.postgres.PostgresSaver.aput_writes</code>)")` | 

Asynchronously store intermediate writes linked to a checkpoint.



 |
| `[adelete_thread](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.postgres.PostgresSaver.adelete_thread "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">adelete_thread</span> <span class="doc doc-labels"> <small class="doc doc-label doc-label-async"><code>async</code></small> </span> (<code>langgraph.checkpoint.postgres.PostgresSaver.adelete_thread</code>)")` | 

Delete all checkpoints and writes associated with a specific thread ID.



 |

Attributes:

| Name | Type | Description |
| --- | --- | --- |
| `[config_specs](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.postgres.PostgresSaver.config_specs "<code class="doc-symbol doc-symbol-heading doc-symbol-attribute"></code>            <span class="doc doc-object-name doc-attribute-name">config_specs</span> <span class="doc doc-labels"> <small class="doc doc-label doc-label-property"><code>property</code></small> </span> (<code>langgraph.checkpoint.postgres.PostgresSaver.config_specs</code>)")` | `[list](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.BaseCheckpointSaver.list "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">list</span> (<code>langgraph.checkpoint.base.BaseCheckpointSaver.list</code>)")` | 
Define the configuration options for the checkpoint saver.



 |

### config\_specs `property` [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.postgres.PostgresSaver.config_specs "Permanent link")

```
config_specs: list

```

Define the configuration options for the checkpoint saver.

Returns:

| Name | Type | Description |
| --- | --- | --- |
| `list` | `[list](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.BaseCheckpointSaver.list "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">list</span> (<code>langgraph.checkpoint.base.BaseCheckpointSaver.list</code>)")` | 
List of configuration field specs.



 |

### from\_conn\_string `classmethod` [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.postgres.PostgresSaver.from_conn_string "Permanent link")

```
from_conn_string(
    conn_string: str, *, pipeline: bool = False
) -> Iterator[PostgresSaver]

```

Create a new PostgresSaver instance from a connection string.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `conn_string` | `[str](https://docs.python.org/3/library/stdtypes.html#str)` | 
The Postgres connection info string.



 | _required_ |
| `pipeline` | `[bool](https://docs.python.org/3/library/functions.html#bool)` | 

whether to use Pipeline



 | `False` |

Returns:

| Name | Type | Description |
| --- | --- | --- |
| `PostgresSaver` | `[Iterator](https://docs.python.org/3/library/collections.abc.html#collections.abc.Iterator "<code>collections.abc.Iterator</code>")[[PostgresSaver](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.postgres.PostgresSaver "<code class="doc-symbol doc-symbol-heading doc-symbol-class"></code>            <span class="doc doc-object-name doc-class-name">PostgresSaver</span> (<code>langgraph.checkpoint.postgres.PostgresSaver</code>)")]` | 
A new PostgresSaver instance.



 |

### setup [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.postgres.PostgresSaver.setup "Permanent link")

```
setup() -> None

```

Set up the checkpoint database asynchronously.

This method creates the necessary tables in the Postgres database if they don't already exist and runs database migrations. It MUST be called directly by the user the first time checkpointer is used.

### list [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.postgres.PostgresSaver.list "Permanent link")

```
list(
    config: RunnableConfig | None,
    *,
    filter: dict[str, Any] | None = None,
    before: RunnableConfig | None = None,
    limit: int | None = None
) -> Iterator[CheckpointTuple]

```

List checkpoints from the database.

This method retrieves a list of checkpoint tuples from the Postgres database based on the provided config. The checkpoints are ordered by checkpoint ID in descending order (newest first).

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `config` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>") | None` | 
The config to use for listing the checkpoints.



 | _required_ |
| `filter` | `[dict](https://docs.python.org/3/library/stdtypes.html#dict)[[str](https://docs.python.org/3/library/stdtypes.html#str), [Any](https://docs.python.org/3/library/typing.html#typing.Any "<code>typing.Any</code>")] | None` | 

Additional filtering criteria for metadata. Defaults to None.



 | `None` |
| `before` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>") | None` | 

If provided, only checkpoints before the specified checkpoint ID are returned. Defaults to None.



 | `None` |
| `limit` | `[int](https://docs.python.org/3/library/functions.html#int) | None` | 

The maximum number of checkpoints to return. Defaults to None.



 | `None` |

Yields:

| Type | Description |
| --- | --- |
| `CheckpointTuple` | 
Iterator\[CheckpointTuple\]: An iterator of checkpoint tuples.



 |

Examples:

```
>>> from langgraph.checkpoint.postgres import PostgresSaver
>>> DB_URI = "postgres://postgres:postgres@localhost:5432/postgres?sslmode=disable"
>>> with PostgresSaver.from_conn_string(DB_URI) as memory:
... # Run a graph, then list the checkpoints
>>>     config = {"configurable": {"thread_id": "1"}}
>>>     checkpoints = list(memory.list(config, limit=2))
>>> print(checkpoints)
[CheckpointTuple(...), CheckpointTuple(...)]

```

```
>>> config = {"configurable": {"thread_id": "1"}}
>>> before = {"configurable": {"checkpoint_id": "1ef4f797-8335-6428-8001-8a1503f9b875"}}
>>> with PostgresSaver.from_conn_string(DB_URI) as memory:
... # Run a graph, then list the checkpoints
>>>     checkpoints = list(memory.list(config, before=before))
>>> print(checkpoints)
[CheckpointTuple(...), ...]

```

### get\_tuple [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.postgres.PostgresSaver.get_tuple "Permanent link")

```
get_tuple(config: RunnableConfig) -> CheckpointTuple | None

```

Get a checkpoint tuple from the database.

This method retrieves a checkpoint tuple from the Postgres database based on the provided config. If the config contains a "checkpoint\_id" key, the checkpoint with the matching thread ID and timestamp is retrieved. Otherwise, the latest checkpoint for the given thread ID is retrieved.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `config` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>")` | 
The config to use for retrieving the checkpoint.



 | _required_ |

Returns:

| Type | Description |
| --- | --- |
| `CheckpointTuple | None` | 
Optional\[CheckpointTuple\]: The retrieved checkpoint tuple, or None if no matching checkpoint was found.



 |

Examples:

```
Basic:
>>> config = {"configurable": {"thread_id": "1"}}
>>> checkpoint_tuple = memory.get_tuple(config)
>>> print(checkpoint_tuple)
CheckpointTuple(...)

With timestamp:

>>> config = {
...    "configurable": {
...        "thread_id": "1",
...        "checkpoint_ns": "",
...        "checkpoint_id": "1ef4f797-8335-6428-8001-8a1503f9b875",
...    }
... }
>>> checkpoint_tuple = memory.get_tuple(config)
>>> print(checkpoint_tuple)
CheckpointTuple(...)

```

### put [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.postgres.PostgresSaver.put "Permanent link")

```
put(
    config: RunnableConfig,
    checkpoint: Checkpoint,
    metadata: CheckpointMetadata,
    new_versions: ChannelVersions,
) -> RunnableConfig

```

Save a checkpoint to the database.

This method saves a checkpoint to the Postgres database. The checkpoint is associated with the provided config and its parent config (if any).

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `config` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>")` | 
The config to associate with the checkpoint.



 | _required_ |
| `checkpoint` | `[Checkpoint](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.Checkpoint "<code class="doc-symbol doc-symbol-heading doc-symbol-class"></code>            <span class="doc doc-object-name doc-class-name">Checkpoint</span> (<code>langgraph.checkpoint.base.Checkpoint</code>)")` | 

The checkpoint to save.



 | _required_ |
| `metadata` | `[CheckpointMetadata](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.CheckpointMetadata "<code class="doc-symbol doc-symbol-heading doc-symbol-class"></code>            <span class="doc doc-object-name doc-class-name">CheckpointMetadata</span> (<code>langgraph.checkpoint.base.CheckpointMetadata</code>)")` | 

Additional metadata to save with the checkpoint.



 | _required_ |
| `new_versions` | `ChannelVersions` | 

New channel versions as of this write.



 | _required_ |

Returns:

| Name | Type | Description |
| --- | --- | --- |
| `RunnableConfig` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>")` | 
Updated configuration after storing the checkpoint.



 |

Examples:

```
>>> from langgraph.checkpoint.postgres import PostgresSaver
>>> DB_URI = "postgres://postgres:postgres@localhost:5432/postgres?sslmode=disable"
>>> with PostgresSaver.from_conn_string(DB_URI) as memory:
>>>     config = {"configurable": {"thread_id": "1", "checkpoint_ns": ""}}
>>>     checkpoint = {"ts": "2024-05-04T06:32:42.235444+00:00", "id": "1ef4f797-8335-6428-8001-8a1503f9b875", "channel_values": {"key": "value"}}
>>>     saved_config = memory.put(config, checkpoint, {"source": "input", "step": 1, "writes": {"key": "value"}}, {})
>>> print(saved_config)
{'configurable': {'thread_id': '1', 'checkpoint_ns': '', 'checkpoint_id': '1ef4f797-8335-6428-8001-8a1503f9b875'}}

```

### put\_writes [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.postgres.PostgresSaver.put_writes "Permanent link")

```
put_writes(
    config: RunnableConfig,
    writes: Sequence[tuple[str, Any]],
    task_id: str,
    task_path: str = "",
) -> None

```

Store intermediate writes linked to a checkpoint.

This method saves intermediate writes associated with a checkpoint to the Postgres database.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `config` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>")` | 
Configuration of the related checkpoint.



 | _required_ |
| `writes` | `[Sequence](https://docs.python.org/3/library/collections.abc.html#collections.abc.Sequence "<code>collections.abc.Sequence</code>")[[tuple](https://docs.python.org/3/library/stdtypes.html#tuple)[[str](https://docs.python.org/3/library/stdtypes.html#str), [Any](https://docs.python.org/3/library/typing.html#typing.Any "<code>typing.Any</code>")]]` | 

List of writes to store.



 | _required_ |
| `task_id` | `[str](https://docs.python.org/3/library/stdtypes.html#str)` | 

Identifier for the task creating the writes.



 | _required_ |

### delete\_thread [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.postgres.PostgresSaver.delete_thread "Permanent link")

```
delete_thread(thread_id: str) -> None

```

Delete all checkpoints and writes associated with a thread ID.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `thread_id` | `[str](https://docs.python.org/3/library/stdtypes.html#str)` | 
The thread ID to delete.



 | _required_ |

Returns:

| Type | Description |
| --- | --- |
| `None` | 
None



 |

### get [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.postgres.PostgresSaver.get "Permanent link")

```
get(config: RunnableConfig) -> Checkpoint | None

```

Fetch a checkpoint using the given configuration.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `config` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>")` | 
Configuration specifying which checkpoint to retrieve.



 | _required_ |

Returns:

| Type | Description |
| --- | --- |
| `[Checkpoint](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.Checkpoint "<code class="doc-symbol doc-symbol-heading doc-symbol-class"></code>            <span class="doc doc-object-name doc-class-name">Checkpoint</span> (<code>langgraph.checkpoint.base.Checkpoint</code>)") | None` | 
Optional\[Checkpoint\]: The requested checkpoint, or None if not found.



 |

### aget `async` [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.postgres.PostgresSaver.aget "Permanent link")

```
aget(config: RunnableConfig) -> Checkpoint | None

```

Asynchronously fetch a checkpoint using the given configuration.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `config` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>")` | 
Configuration specifying which checkpoint to retrieve.



 | _required_ |

Returns:

| Type | Description |
| --- | --- |
| `[Checkpoint](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.Checkpoint "<code class="doc-symbol doc-symbol-heading doc-symbol-class"></code>            <span class="doc doc-object-name doc-class-name">Checkpoint</span> (<code>langgraph.checkpoint.base.Checkpoint</code>)") | None` | 
Optional\[Checkpoint\]: The requested checkpoint, or None if not found.



 |

### aget\_tuple `async` [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.postgres.PostgresSaver.aget_tuple "Permanent link")

```
aget_tuple(
    config: RunnableConfig,
) -> CheckpointTuple | None

```

Asynchronously fetch a checkpoint tuple using the given configuration.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `config` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>")` | 
Configuration specifying which checkpoint to retrieve.



 | _required_ |

Returns:

| Type | Description |
| --- | --- |
| `CheckpointTuple | None` | 
Optional\[CheckpointTuple\]: The requested checkpoint tuple, or None if not found.



 |

Raises:

| Type | Description |
| --- | --- |
| `[NotImplementedError](https://docs.python.org/3/library/exceptions.html#NotImplementedError)` | 
Implement this method in your custom checkpoint saver.



 |

### alist `async` [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.postgres.PostgresSaver.alist "Permanent link")

```
alist(
    config: RunnableConfig | None,
    *,
    filter: dict[str, Any] | None = None,
    before: RunnableConfig | None = None,
    limit: int | None = None
) -> AsyncIterator[CheckpointTuple]

```

Asynchronously list checkpoints that match the given criteria.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `config` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>") | None` | 
Base configuration for filtering checkpoints.



 | _required_ |
| `filter` | `[dict](https://docs.python.org/3/library/stdtypes.html#dict)[[str](https://docs.python.org/3/library/stdtypes.html#str), [Any](https://docs.python.org/3/library/typing.html#typing.Any "<code>typing.Any</code>")] | None` | 

Additional filtering criteria for metadata.



 | `None` |
| `before` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>") | None` | 

List checkpoints created before this configuration.



 | `None` |
| `limit` | `[int](https://docs.python.org/3/library/functions.html#int) | None` | 

Maximum number of checkpoints to return.



 | `None` |

Returns:

| Type | Description |
| --- | --- |
| `[AsyncIterator](https://docs.python.org/3/library/collections.abc.html#collections.abc.AsyncIterator "<code>collections.abc.AsyncIterator</code>")[CheckpointTuple]` | 
AsyncIterator\[CheckpointTuple\]: Async iterator of matching checkpoint tuples.



 |

Raises:

| Type | Description |
| --- | --- |
| `[NotImplementedError](https://docs.python.org/3/library/exceptions.html#NotImplementedError)` | 
Implement this method in your custom checkpoint saver.



 |

### aput `async` [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.postgres.PostgresSaver.aput "Permanent link")

```
aput(
    config: RunnableConfig,
    checkpoint: Checkpoint,
    metadata: CheckpointMetadata,
    new_versions: ChannelVersions,
) -> RunnableConfig

```

Asynchronously store a checkpoint with its configuration and metadata.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `config` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>")` | 
Configuration for the checkpoint.



 | _required_ |
| `checkpoint` | `[Checkpoint](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.Checkpoint "<code class="doc-symbol doc-symbol-heading doc-symbol-class"></code>            <span class="doc doc-object-name doc-class-name">Checkpoint</span> (<code>langgraph.checkpoint.base.Checkpoint</code>)")` | 

The checkpoint to store.



 | _required_ |
| `metadata` | `[CheckpointMetadata](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.CheckpointMetadata "<code class="doc-symbol doc-symbol-heading doc-symbol-class"></code>            <span class="doc doc-object-name doc-class-name">CheckpointMetadata</span> (<code>langgraph.checkpoint.base.CheckpointMetadata</code>)")` | 

Additional metadata for the checkpoint.



 | _required_ |
| `new_versions` | `ChannelVersions` | 

New channel versions as of this write.



 | _required_ |

Returns:

| Name | Type | Description |
| --- | --- | --- |
| `RunnableConfig` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>")` | 
Updated configuration after storing the checkpoint.



 |

Raises:

| Type | Description |
| --- | --- |
| `[NotImplementedError](https://docs.python.org/3/library/exceptions.html#NotImplementedError)` | 
Implement this method in your custom checkpoint saver.



 |

### aput\_writes `async` [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.postgres.PostgresSaver.aput_writes "Permanent link")

```
aput_writes(
    config: RunnableConfig,
    writes: Sequence[tuple[str, Any]],
    task_id: str,
    task_path: str = "",
) -> None

```

Asynchronously store intermediate writes linked to a checkpoint.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `config` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>")` | 
Configuration of the related checkpoint.



 | _required_ |
| `writes` | `[Sequence](https://docs.python.org/3/library/collections.abc.html#collections.abc.Sequence "<code>collections.abc.Sequence</code>")[[tuple](https://docs.python.org/3/library/stdtypes.html#tuple)[[str](https://docs.python.org/3/library/stdtypes.html#str), [Any](https://docs.python.org/3/library/typing.html#typing.Any "<code>typing.Any</code>")]]` | 

List of writes to store.



 | _required_ |
| `task_id` | `[str](https://docs.python.org/3/library/stdtypes.html#str)` | 

Identifier for the task creating the writes.



 | _required_ |
| `task_path` | `[str](https://docs.python.org/3/library/stdtypes.html#str)` | 

Path of the task creating the writes.



 | `''` |

Raises:

| Type | Description |
| --- | --- |
| `[NotImplementedError](https://docs.python.org/3/library/exceptions.html#NotImplementedError)` | 
Implement this method in your custom checkpoint saver.



 |

### adelete\_thread `async` [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.postgres.PostgresSaver.adelete_thread "Permanent link")

```
adelete_thread(thread_id: str) -> None

```

Delete all checkpoints and writes associated with a specific thread ID.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `thread_id` | `[str](https://docs.python.org/3/library/stdtypes.html#str)` | 
The thread ID whose checkpoints should be deleted.



 | _required_ |

Classes:

| Name | Description |
| --- | --- |
| `[AsyncPostgresSaver](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.postgres.aio.AsyncPostgresSaver "<code class="doc-symbol doc-symbol-heading doc-symbol-class"></code>            <span class="doc doc-object-name doc-class-name">AsyncPostgresSaver</span> (<code>langgraph.checkpoint.postgres.aio.AsyncPostgresSaver</code>)")` | 
Asynchronous checkpointer that stores checkpoints in a Postgres database.



 |

## AsyncPostgresSaver [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.postgres.aio.AsyncPostgresSaver "Permanent link")

Bases: `BasePostgresSaver`

Asynchronous checkpointer that stores checkpoints in a Postgres database.

Methods:

| Name | Description |
| --- | --- |
| `[from_conn_string](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.postgres.aio.AsyncPostgresSaver.from_conn_string "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">from_conn_string</span> <span class="doc doc-labels"> <small class="doc doc-label doc-label-async"><code>async</code></small> <small class="doc doc-label doc-label-classmethod"><code>classmethod</code></small> </span> (<code>langgraph.checkpoint.postgres.aio.AsyncPostgresSaver.from_conn_string</code>)")` | 
Create a new AsyncPostgresSaver instance from a connection string.



 |
| `[setup](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.postgres.aio.AsyncPostgresSaver.setup "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">setup</span> <span class="doc doc-labels"> <small class="doc doc-label doc-label-async"><code>async</code></small> </span> (<code>langgraph.checkpoint.postgres.aio.AsyncPostgresSaver.setup</code>)")` | 

Set up the checkpoint database asynchronously.



 |
| `[alist](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.postgres.aio.AsyncPostgresSaver.alist "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">alist</span> <span class="doc doc-labels"> <small class="doc doc-label doc-label-async"><code>async</code></small> </span> (<code>langgraph.checkpoint.postgres.aio.AsyncPostgresSaver.alist</code>)")` | 

List checkpoints from the database asynchronously.



 |
| `[aget_tuple](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.postgres.aio.AsyncPostgresSaver.aget_tuple "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">aget_tuple</span> <span class="doc doc-labels"> <small class="doc doc-label doc-label-async"><code>async</code></small> </span> (<code>langgraph.checkpoint.postgres.aio.AsyncPostgresSaver.aget_tuple</code>)")` | 

Get a checkpoint tuple from the database asynchronously.



 |
| `[aput](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.postgres.aio.AsyncPostgresSaver.aput "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">aput</span> <span class="doc doc-labels"> <small class="doc doc-label doc-label-async"><code>async</code></small> </span> (<code>langgraph.checkpoint.postgres.aio.AsyncPostgresSaver.aput</code>)")` | 

Save a checkpoint to the database asynchronously.



 |
| `[aput_writes](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.postgres.aio.AsyncPostgresSaver.aput_writes "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">aput_writes</span> <span class="doc doc-labels"> <small class="doc doc-label doc-label-async"><code>async</code></small> </span> (<code>langgraph.checkpoint.postgres.aio.AsyncPostgresSaver.aput_writes</code>)")` | 

Store intermediate writes linked to a checkpoint asynchronously.



 |
| `[adelete_thread](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.postgres.aio.AsyncPostgresSaver.adelete_thread "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">adelete_thread</span> <span class="doc doc-labels"> <small class="doc doc-label doc-label-async"><code>async</code></small> </span> (<code>langgraph.checkpoint.postgres.aio.AsyncPostgresSaver.adelete_thread</code>)")` | 

Delete all checkpoints and writes associated with a thread ID.



 |
| `[list](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.postgres.aio.AsyncPostgresSaver.list "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">list</span> (<code>langgraph.checkpoint.postgres.aio.AsyncPostgresSaver.list</code>)")` | 

List checkpoints from the database.



 |
| `[get_tuple](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.postgres.aio.AsyncPostgresSaver.get_tuple "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">get_tuple</span> (<code>langgraph.checkpoint.postgres.aio.AsyncPostgresSaver.get_tuple</code>)")` | 

Get a checkpoint tuple from the database.



 |
| `[put](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.postgres.aio.AsyncPostgresSaver.put "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">put</span> (<code>langgraph.checkpoint.postgres.aio.AsyncPostgresSaver.put</code>)")` | 

Save a checkpoint to the database.



 |
| `[put_writes](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.postgres.aio.AsyncPostgresSaver.put_writes "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">put_writes</span> (<code>langgraph.checkpoint.postgres.aio.AsyncPostgresSaver.put_writes</code>)")` | 

Store intermediate writes linked to a checkpoint.



 |
| `[delete_thread](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.postgres.aio.AsyncPostgresSaver.delete_thread "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">delete_thread</span> (<code>langgraph.checkpoint.postgres.aio.AsyncPostgresSaver.delete_thread</code>)")` | 

Delete all checkpoints and writes associated with a thread ID.



 |
| `[get](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.postgres.aio.AsyncPostgresSaver.get "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">get</span> (<code>langgraph.checkpoint.postgres.aio.AsyncPostgresSaver.get</code>)")` | 

Fetch a checkpoint using the given configuration.



 |
| `[aget](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.postgres.aio.AsyncPostgresSaver.aget "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">aget</span> <span class="doc doc-labels"> <small class="doc doc-label doc-label-async"><code>async</code></small> </span> (<code>langgraph.checkpoint.postgres.aio.AsyncPostgresSaver.aget</code>)")` | 

Asynchronously fetch a checkpoint using the given configuration.



 |

Attributes:

| Name | Type | Description |
| --- | --- | --- |
| `[config_specs](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.postgres.aio.AsyncPostgresSaver.config_specs "<code class="doc-symbol doc-symbol-heading doc-symbol-attribute"></code>            <span class="doc doc-object-name doc-attribute-name">config_specs</span> <span class="doc doc-labels"> <small class="doc doc-label doc-label-property"><code>property</code></small> </span> (<code>langgraph.checkpoint.postgres.aio.AsyncPostgresSaver.config_specs</code>)")` | `[list](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.BaseCheckpointSaver.list "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">list</span> (<code>langgraph.checkpoint.base.BaseCheckpointSaver.list</code>)")` | 
Define the configuration options for the checkpoint saver.



 |

### config\_specs `property` [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.postgres.aio.AsyncPostgresSaver.config_specs "Permanent link")

```
config_specs: list

```

Define the configuration options for the checkpoint saver.

Returns:

| Name | Type | Description |
| --- | --- | --- |
| `list` | `[list](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.BaseCheckpointSaver.list "<code class="doc-symbol doc-symbol-heading doc-symbol-method"></code>            <span class="doc doc-object-name doc-function-name">list</span> (<code>langgraph.checkpoint.base.BaseCheckpointSaver.list</code>)")` | 
List of configuration field specs.



 |

### from\_conn\_string `async` `classmethod` [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.postgres.aio.AsyncPostgresSaver.from_conn_string "Permanent link")

```
from_conn_string(
    conn_string: str,
    *,
    pipeline: bool = False,
    serde: SerializerProtocol | None = None
) -> AsyncIterator[AsyncPostgresSaver]

```

Create a new AsyncPostgresSaver instance from a connection string.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `conn_string` | `[str](https://docs.python.org/3/library/stdtypes.html#str)` | 
The Postgres connection info string.



 | _required_ |
| `pipeline` | `[bool](https://docs.python.org/3/library/functions.html#bool)` | 

whether to use AsyncPipeline



 | `False` |

Returns:

| Name | Type | Description |
| --- | --- | --- |
| `AsyncPostgresSaver` | `[AsyncIterator](https://docs.python.org/3/library/collections.abc.html#collections.abc.AsyncIterator "<code>collections.abc.AsyncIterator</code>")[[AsyncPostgresSaver](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.postgres.aio.AsyncPostgresSaver "<code class="doc-symbol doc-symbol-heading doc-symbol-class"></code>            <span class="doc doc-object-name doc-class-name">AsyncPostgresSaver</span> (<code>langgraph.checkpoint.postgres.aio.AsyncPostgresSaver</code>)")]` | 
A new AsyncPostgresSaver instance.



 |

### setup `async` [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.postgres.aio.AsyncPostgresSaver.setup "Permanent link")

```
setup() -> None

```

Set up the checkpoint database asynchronously.

This method creates the necessary tables in the Postgres database if they don't already exist and runs database migrations. It MUST be called directly by the user the first time checkpointer is used.

### alist `async` [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.postgres.aio.AsyncPostgresSaver.alist "Permanent link")

```
alist(
    config: RunnableConfig | None,
    *,
    filter: dict[str, Any] | None = None,
    before: RunnableConfig | None = None,
    limit: int | None = None
) -> AsyncIterator[CheckpointTuple]

```

List checkpoints from the database asynchronously.

This method retrieves a list of checkpoint tuples from the Postgres database based on the provided config. The checkpoints are ordered by checkpoint ID in descending order (newest first).

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `config` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>") | None` | 
Base configuration for filtering checkpoints.



 | _required_ |
| `filter` | `[dict](https://docs.python.org/3/library/stdtypes.html#dict)[[str](https://docs.python.org/3/library/stdtypes.html#str), [Any](https://docs.python.org/3/library/typing.html#typing.Any "<code>typing.Any</code>")] | None` | 

Additional filtering criteria for metadata.



 | `None` |
| `before` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>") | None` | 

If provided, only checkpoints before the specified checkpoint ID are returned. Defaults to None.



 | `None` |
| `limit` | `[int](https://docs.python.org/3/library/functions.html#int) | None` | 

Maximum number of checkpoints to return.



 | `None` |

Yields:

| Type | Description |
| --- | --- |
| `[AsyncIterator](https://docs.python.org/3/library/collections.abc.html#collections.abc.AsyncIterator "<code>collections.abc.AsyncIterator</code>")[CheckpointTuple]` | 
AsyncIterator\[CheckpointTuple\]: An asynchronous iterator of matching checkpoint tuples.



 |

### aget\_tuple `async` [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.postgres.aio.AsyncPostgresSaver.aget_tuple "Permanent link")

```
aget_tuple(
    config: RunnableConfig,
) -> CheckpointTuple | None

```

Get a checkpoint tuple from the database asynchronously.

This method retrieves a checkpoint tuple from the Postgres database based on the provided config. If the config contains a "checkpoint\_id" key, the checkpoint with the matching thread ID and "checkpoint\_id" is retrieved. Otherwise, the latest checkpoint for the given thread ID is retrieved.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `config` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>")` | 
The config to use for retrieving the checkpoint.



 | _required_ |

Returns:

| Type | Description |
| --- | --- |
| `CheckpointTuple | None` | 
Optional\[CheckpointTuple\]: The retrieved checkpoint tuple, or None if no matching checkpoint was found.



 |

### aput `async` [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.postgres.aio.AsyncPostgresSaver.aput "Permanent link")

```
aput(
    config: RunnableConfig,
    checkpoint: Checkpoint,
    metadata: CheckpointMetadata,
    new_versions: ChannelVersions,
) -> RunnableConfig

```

Save a checkpoint to the database asynchronously.

This method saves a checkpoint to the Postgres database. The checkpoint is associated with the provided config and its parent config (if any).

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `config` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>")` | 
The config to associate with the checkpoint.



 | _required_ |
| `checkpoint` | `[Checkpoint](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.Checkpoint "<code class="doc-symbol doc-symbol-heading doc-symbol-class"></code>            <span class="doc doc-object-name doc-class-name">Checkpoint</span> (<code>langgraph.checkpoint.base.Checkpoint</code>)")` | 

The checkpoint to save.



 | _required_ |
| `metadata` | `[CheckpointMetadata](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.CheckpointMetadata "<code class="doc-symbol doc-symbol-heading doc-symbol-class"></code>            <span class="doc doc-object-name doc-class-name">CheckpointMetadata</span> (<code>langgraph.checkpoint.base.CheckpointMetadata</code>)")` | 

Additional metadata to save with the checkpoint.



 | _required_ |
| `new_versions` | `ChannelVersions` | 

New channel versions as of this write.



 | _required_ |

Returns:

| Name | Type | Description |
| --- | --- | --- |
| `RunnableConfig` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>")` | 
Updated configuration after storing the checkpoint.



 |

### aput\_writes `async` [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.postgres.aio.AsyncPostgresSaver.aput_writes "Permanent link")

```
aput_writes(
    config: RunnableConfig,
    writes: Sequence[tuple[str, Any]],
    task_id: str,
    task_path: str = "",
) -> None

```

Store intermediate writes linked to a checkpoint asynchronously.

This method saves intermediate writes associated with a checkpoint to the database.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `config` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>")` | 
Configuration of the related checkpoint.



 | _required_ |
| `writes` | `[Sequence](https://docs.python.org/3/library/collections.abc.html#collections.abc.Sequence "<code>collections.abc.Sequence</code>")[[tuple](https://docs.python.org/3/library/stdtypes.html#tuple)[[str](https://docs.python.org/3/library/stdtypes.html#str), [Any](https://docs.python.org/3/library/typing.html#typing.Any "<code>typing.Any</code>")]]` | 

List of writes to store, each as (channel, value) pair.



 | _required_ |
| `task_id` | `[str](https://docs.python.org/3/library/stdtypes.html#str)` | 

Identifier for the task creating the writes.



 | _required_ |

### adelete\_thread `async` [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.postgres.aio.AsyncPostgresSaver.adelete_thread "Permanent link")

```
adelete_thread(thread_id: str) -> None

```

Delete all checkpoints and writes associated with a thread ID.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `thread_id` | `[str](https://docs.python.org/3/library/stdtypes.html#str)` | 
The thread ID to delete.



 | _required_ |

Returns:

| Type | Description |
| --- | --- |
| `None` | 
None



 |

### list [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.postgres.aio.AsyncPostgresSaver.list "Permanent link")

```
list(
    config: RunnableConfig | None,
    *,
    filter: dict[str, Any] | None = None,
    before: RunnableConfig | None = None,
    limit: int | None = None
) -> Iterator[CheckpointTuple]

```

List checkpoints from the database.

This method retrieves a list of checkpoint tuples from the Postgres database based on the provided config. The checkpoints are ordered by checkpoint ID in descending order (newest first).

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `config` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>") | None` | 
Base configuration for filtering checkpoints.



 | _required_ |
| `filter` | `[dict](https://docs.python.org/3/library/stdtypes.html#dict)[[str](https://docs.python.org/3/library/stdtypes.html#str), [Any](https://docs.python.org/3/library/typing.html#typing.Any "<code>typing.Any</code>")] | None` | 

Additional filtering criteria for metadata.



 | `None` |
| `before` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>") | None` | 

If provided, only checkpoints before the specified checkpoint ID are returned. Defaults to None.



 | `None` |
| `limit` | `[int](https://docs.python.org/3/library/functions.html#int) | None` | 

Maximum number of checkpoints to return.



 | `None` |

Yields:

| Type | Description |
| --- | --- |
| `CheckpointTuple` | 
Iterator\[CheckpointTuple\]: An iterator of matching checkpoint tuples.



 |

### get\_tuple [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.postgres.aio.AsyncPostgresSaver.get_tuple "Permanent link")

```
get_tuple(config: RunnableConfig) -> CheckpointTuple | None

```

Get a checkpoint tuple from the database.

This method retrieves a checkpoint tuple from the Postgres database based on the provided config. If the config contains a "checkpoint\_id" key, the checkpoint with the matching thread ID and "checkpoint\_id" is retrieved. Otherwise, the latest checkpoint for the given thread ID is retrieved.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `config` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>")` | 
The config to use for retrieving the checkpoint.



 | _required_ |

Returns:

| Type | Description |
| --- | --- |
| `CheckpointTuple | None` | 
Optional\[CheckpointTuple\]: The retrieved checkpoint tuple, or None if no matching checkpoint was found.



 |

### put [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.postgres.aio.AsyncPostgresSaver.put "Permanent link")

```
put(
    config: RunnableConfig,
    checkpoint: Checkpoint,
    metadata: CheckpointMetadata,
    new_versions: ChannelVersions,
) -> RunnableConfig

```

Save a checkpoint to the database.

This method saves a checkpoint to the Postgres database. The checkpoint is associated with the provided config and its parent config (if any).

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `config` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>")` | 
The config to associate with the checkpoint.



 | _required_ |
| `checkpoint` | `[Checkpoint](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.Checkpoint "<code class="doc-symbol doc-symbol-heading doc-symbol-class"></code>            <span class="doc doc-object-name doc-class-name">Checkpoint</span> (<code>langgraph.checkpoint.base.Checkpoint</code>)")` | 

The checkpoint to save.



 | _required_ |
| `metadata` | `[CheckpointMetadata](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.CheckpointMetadata "<code class="doc-symbol doc-symbol-heading doc-symbol-class"></code>            <span class="doc doc-object-name doc-class-name">CheckpointMetadata</span> (<code>langgraph.checkpoint.base.CheckpointMetadata</code>)")` | 

Additional metadata to save with the checkpoint.



 | _required_ |
| `new_versions` | `ChannelVersions` | 

New channel versions as of this write.



 | _required_ |

Returns:

| Name | Type | Description |
| --- | --- | --- |
| `RunnableConfig` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>")` | 
Updated configuration after storing the checkpoint.



 |

### put\_writes [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.postgres.aio.AsyncPostgresSaver.put_writes "Permanent link")

```
put_writes(
    config: RunnableConfig,
    writes: Sequence[tuple[str, Any]],
    task_id: str,
    task_path: str = "",
) -> None

```

Store intermediate writes linked to a checkpoint.

This method saves intermediate writes associated with a checkpoint to the database.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `config` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>")` | 
Configuration of the related checkpoint.



 | _required_ |
| `writes` | `[Sequence](https://docs.python.org/3/library/collections.abc.html#collections.abc.Sequence "<code>collections.abc.Sequence</code>")[[tuple](https://docs.python.org/3/library/stdtypes.html#tuple)[[str](https://docs.python.org/3/library/stdtypes.html#str), [Any](https://docs.python.org/3/library/typing.html#typing.Any "<code>typing.Any</code>")]]` | 

List of writes to store, each as (channel, value) pair.



 | _required_ |
| `task_id` | `[str](https://docs.python.org/3/library/stdtypes.html#str)` | 

Identifier for the task creating the writes.



 | _required_ |
| `task_path` | `[str](https://docs.python.org/3/library/stdtypes.html#str)` | 

Path of the task creating the writes.



 | `''` |

### delete\_thread [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.postgres.aio.AsyncPostgresSaver.delete_thread "Permanent link")

```
delete_thread(thread_id: str) -> None

```

Delete all checkpoints and writes associated with a thread ID.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `thread_id` | `[str](https://docs.python.org/3/library/stdtypes.html#str)` | 
The thread ID to delete.



 | _required_ |

Returns:

| Type | Description |
| --- | --- |
| `None` | 
None



 |

### get [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.postgres.aio.AsyncPostgresSaver.get "Permanent link")

```
get(config: RunnableConfig) -> Checkpoint | None

```

Fetch a checkpoint using the given configuration.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `config` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>")` | 
Configuration specifying which checkpoint to retrieve.



 | _required_ |

Returns:

| Type | Description |
| --- | --- |
| `[Checkpoint](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.Checkpoint "<code class="doc-symbol doc-symbol-heading doc-symbol-class"></code>            <span class="doc doc-object-name doc-class-name">Checkpoint</span> (<code>langgraph.checkpoint.base.Checkpoint</code>)") | None` | 
Optional\[Checkpoint\]: The requested checkpoint, or None if not found.



 |

### aget `async` [¶](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.postgres.aio.AsyncPostgresSaver.aget "Permanent link")

```
aget(config: RunnableConfig) -> Checkpoint | None

```

Asynchronously fetch a checkpoint using the given configuration.

Parameters:

| Name | Type | Description | Default |
| --- | --- | --- | --- |
| `config` | `[RunnableConfig](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.config.RunnableConfig.html#langchain_core.runnables.config.RunnableConfig "<code>langchain_core.runnables.RunnableConfig</code>")` | 
Configuration specifying which checkpoint to retrieve.



 | _required_ |

Returns:

| Type | Description |
| --- | --- |
| `[Checkpoint](https://langchain-ai.github.io/langgraph/reference/checkpoints//#langgraph.checkpoint.base.Checkpoint "<code class="doc-symbol doc-symbol-heading doc-symbol-class"></code>            <span class="doc doc-object-name doc-class-name">Checkpoint</span> (<code>langgraph.checkpoint.base.Checkpoint</code>)") | None` | 
Optional\[Checkpoint\]: The requested checkpoint, or None if not found.



 |