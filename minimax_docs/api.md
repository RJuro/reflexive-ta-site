> ## Documentation Index
> Fetch the complete documentation index at: https://platform.minimax.io/docs/llms.txt
> Use this file to discover all available pages before exploring further.

# Model Invocation

> MiniMax LLMs, supporting multilingual programming, Agent workflows and complex task scenarios.

<Note>
  Subscribe to [Token Plan](https://platform.minimax.io/subscribe/token-plan) to use MiniMax models of all modalities at ultra-low prices!
</Note>

## Model Overview

MiniMax offers multiple LLMs to meet different scenario requirements. **MiniMax-M3** is the latest M-series language model for agentic reasoning, tool use, coding, and long-context tasks, while **MiniMax-M2.7** and **MiniMax-M2** remain available for existing workflows.

### Supported Models

| Model Name                  | Context Window | Description                                                                                                                                   |
| :-------------------------- | :------------- | :-------------------------------------------------------------------------------------------------------------------------------------------- |
| MiniMax-M3                  | 1,000,000      | **Frontier multimodal coding model with 1M context window**                                                                                   |
| MiniMax-M2.7                | 204,800        | **Beginning the journey of recursive self-improvement** (output speed approximately 60 tps)                                                   |
| MiniMax-M2.7-highspeed      | 204,800        | **M2.7 Highspeed: Same performance, faster and more agile (output speed approximately 100 tps)**                                              |
| MiniMax-M2.5                | 204,800        | **Peak Performance. Ultimate Value. Master the Complex (output speed approximately 60 tps)**                                                  |
| MiniMax-M2.5-highspeed      | 204,800        | **M2.5 highspeed: Same performance, faster and more agile (output speed approximately 100 tps)**                                              |
| MiniMax-M2.1                | 204,800        | **Powerful Multi-Language Programming Capabilities with Comprehensively Enhanced Programming Experience (output speed approximately 60 tps)** |
| MiniMax-M2.1-highspeed      | 204,800        | **Faster and More Agile (output speed approximately 100 tps)**                                                                                |
| MiniMax-M2                  | 204,800        | **Agentic capabilities, Advanced reasoning**                                                                                                  |
| [M2-her](/guides/text-chat) | 64 K           | **Designed for dialogue scenarios, supporting role-playing and multi-turn conversations**                                                     |

<Note>
  For details on how tps (Tokens Per Second) is calculated, please refer to [FAQ > About APIs](/faq/about-apis#q-how-is-tps-tokens-per-second-calculated-for-text-models).
</Note>

### **MiniMax M3** Key Highlights

<AccordionGroup>
  <Accordion title="1M-token context">
    MiniMax-M3 supports up to a 1,000,000-token context window for long documents, codebases, and multi-step agent sessions.
  </Accordion>

  <Accordion title="Agent and coding workflows">
    MiniMax-M3 is designed for agentic reasoning, tool use, coding, and structured task execution.
  </Accordion>

  <Accordion title="Multimodal chat input">
    OpenAI-compatible Chat Completions support text, image, and video input with `image_url` and `video_url` content parts.
  </Accordion>
</AccordionGroup>

<Note>
  For more model details, please refer to [MiniMax M3](https://www.minimax.io/models/text/m3).
</Note>

***

## URL Configuration

Before calling MiniMax models, prepare the following:

| Field                                          | Value                                                                              |
| :--------------------------------------------- | :--------------------------------------------------------------------------------- |
| `base_url` (Anthropic-compatible, recommended) | `https://api.minimax.io/anthropic`                                                 |
| `base_url` (OpenAI-compatible)                 | `https://api.minimax.io/v1`                                                        |
| `api_key`                                      | [Get Subscription Key](https://platform.minimax.io/user-center/payment/token-plan) |
| `model`                                        | See [Supported Models](#supported-models) above                                    |

***

## Calling Example

MiniMax accepts both Anthropic-style and OpenAI-style request formats. The two examples below are equivalent non-streaming calls; flip `stream` to `true` to switch to streaming responses.

### Anthropic-Compatible (Recommended)

Supports thinking blocks, interleaved thinking, and other advanced features — this is the default path.

<CodeGroup>
  ```bash curl theme={null}
  curl https://api.minimax.io/anthropic/v1/messages \
    -H "Authorization: Bearer <MINIMAX_API_KEY>" \
    -H "Content-Type: application/json" \
    -d '{
      "model": "MiniMax-M3",
      "max_tokens": 1000,
      "messages": [
        {"role": "user", "content": "Hi, how are you?"}
      ]
    }'
  ```

  ```python Python theme={null}
  # Please install the Anthropic SDK first: `pip install anthropic`
  import anthropic

  client = anthropic.Anthropic(
      base_url="https://api.minimax.io/anthropic",
      api_key="<MINIMAX_API_KEY>",
  )

  message = client.messages.create(
      model="MiniMax-M3",
      max_tokens=1000,
      messages=[
          {"role": "user", "content": "Hi, how are you?"}
      ],
  )

  for block in message.content:
      if block.type == "thinking":
          print(f"Thinking:\n{block.thinking}\n")
      elif block.type == "text":
          print(f"Text:\n{block.text}\n")
  ```

  ```javascript Node.js theme={null}
  // Please install the Anthropic SDK first: `npm install @anthropic-ai/sdk`
  import Anthropic from "@anthropic-ai/sdk";

  const client = new Anthropic({
    baseURL: "https://api.minimax.io/anthropic",
    apiKey: "<MINIMAX_API_KEY>",
  });

  const message = await client.messages.create({
    model: "MiniMax-M3",
    max_tokens: 1000,
    messages: [
      { role: "user", content: "Hi, how are you?" },
    ],
  });

  for (const block of message.content) {
    if (block.type === "thinking") {
      console.log(`Thinking:\n${block.thinking}\n`);
    } else if (block.type === "text") {
      console.log(`Text:\n${block.text}\n`);
    }
  }
  ```
</CodeGroup>

### OpenAI-Compatible

Already wired up to the OpenAI SDK? Swap `base_url` and `model` for the values below and you can keep using your existing client without migrating to a new SDK.

<CodeGroup>
  ```bash curl theme={null}
  curl https://api.minimax.io/v1/chat/completions \
    -H "Authorization: Bearer <MINIMAX_API_KEY>" \
    -H "Content-Type: application/json" \
    -d '{
      "model": "MiniMax-M3",
      "messages": [
        {"role": "user", "content": "Hi, how are you?"}
      ]
    }'
  ```

  ```python Python theme={null}
  # Please install the OpenAI SDK first: `pip install openai`
  from openai import OpenAI

  client = OpenAI(
      base_url="https://api.minimax.io/v1",
      api_key="<MINIMAX_API_KEY>",
  )

  response = client.chat.completions.create(
      model="MiniMax-M3",
      messages=[
          {"role": "user", "content": "Hi, how are you?"},
      ],
  )

  print(response.choices[0].message.content)
  ```

  ```javascript Node.js theme={null}
  // Please install the OpenAI SDK first: `npm install openai`
  import OpenAI from "openai";

  const client = new OpenAI({
    baseURL: "https://api.minimax.io/v1",
    apiKey: "<MINIMAX_API_KEY>",
  });

  const response = await client.chat.completions.create({
    model: "MiniMax-M3",
    messages: [
      { role: "user", content: "Hi, how are you?" },
    ],
  });

  console.log(response.choices[0].message.content);
  ```
</CodeGroup>

***

## API Reference

<Columns cols={2}>
  <Card title="Anthropic API Compatible (Recommended)" icon="book-open" href="/api-reference/text-anthropic-api" cta="View Docs">
    Call MiniMax models via Anthropic SDK, supporting streaming output and Interleaved Thinking
  </Card>

  <Card title="OpenAI API Compatible" icon="book-open" href="/api-reference/text-openai-api" cta="View Docs">
    Call MiniMax models via OpenAI SDK
  </Card>

  <Card title="Using M3 in AI Coding Tools" icon="code" href="/token-plan/openclaw" cta="View Docs">
    Use M3 in Claude Code, Cursor and other tools
  </Card>

  <Card title="Chat Model" icon="messages-square" href="/guides/text-chat" cta="View Docs">
    M2-her chat model, designed for role-playing and multi-turn dialogue scenarios
  </Card>
</Columns>

***

## Contact Us

If you encounter any issues while using MiniMax models:

* Contact our technical support team through official channels such as email [Model@minimax.io](mailto:Model@minimax.io)
* Submit an Issue on our [Github](https://github.com/MiniMax-AI/MiniMax-M2.7/issues) repository

## Related Links

* [Anthropic SDK Documentation](https://docs.anthropic.com/en/api/client-sdks)
* [OpenAI SDK Documentation](https://platform.openai.com/docs/libraries)
* [MiniMax M3](https://www.minimax.io/models/text/m3)
