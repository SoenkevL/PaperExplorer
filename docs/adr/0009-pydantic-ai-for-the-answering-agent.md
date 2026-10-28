# The answering agent runs on pydantic-ai; retrieval stays our own SQL

The agent that answers Questions (tool calls, streaming, message history, tracing) is built on pydantic-ai, talking to Ollama through its OpenAI-compatible endpoint. It is typed and pydantic-native like the rest of the code, keeps pyright strict clean, ships `FunctionModel` for offline tests and emits OpenTelemetry spans that Langfuse reads. Retrieval is not handed to the framework: the hybrid RRF query (ADR 0003) is offered to the model as tools.

## Considered Options

- **A hand-written loop:** the most to learn, but streaming tool calls, retries, history and tracing would all be ours to build and keep production-ready.
- **LangGraph:** strong graphs and checkpointing, but it brings `langchain-core`, its typing is looser, and it did not list Python 3.14.
- **OpenAI Agents SDK:** centred on OpenAI, including its default tracing.
- **LlamaIndex, Haystack:** their retrieval and document stores would replace the hand-written SQL.

## Consequences

- The chat model's seam is pydantic-ai's `Model`: the OpenAI-compatible model in use, `FunctionModel` in tests. There is no `ChatModel` port of our own.
- Moving from Ollama to llama.cpp or any other OpenAI-compatible server is a settings change.
