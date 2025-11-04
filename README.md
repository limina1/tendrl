# T̴̢͠E̸͝N̷̛D̸R̴L̵͠
## A Reckless Parasitic Amorphous Configuration Pattern that Drills into Any Nostr client and Injects Custom Feed Capabilities.

> **This is functional satire** its what happens when you combine creative writing, bio horror with software documentation
> **Don't annoy devs** if you use tendrl, don't assume it is good enough to be merged. This is intended for local and personal use only.
> *Like a tendril wrapping around a host, Tendrl drills into any Nostr client and injects custom feed capabilities - recklessly, adaptively, and without asking permission.* 🌿


---

## What is Tendrl?

**Tendrl is not software.**

It's a configuration file. A prompt template. Pure text that convinces an AI to write code that shouldn't exist.

Just `tendrl.toml`, a set of possible prompts
## Requirements

An AI with terminal access and coding capabilities



---


## The Problem 🍄

Your favorite client doesn't show the feeds you want, or maybe it does but you don't like the ui. Maybe one client is super fast, and you want to mix it in with another that looks nice. What if you want to weave various capabilities of different capabilities into a chimera app 🤔⚗️

Every path leads to suffering:

1. **Fork the client** → maintain divergent codebase until heat death (exhaustion)
2. **Submit PR** → wait months for review, probable rejection (dissolution)  
3. **Build new client** → reinvent fifteen thousand lines of networking code (madness)
4. **Give up** → accept limitation forever (death)

There is a fifth option.

***̷̡P̶̀͜A̷͝R̸̛A̵̕͜S̶̾I̵͋T̴̚I̸̊S̶̄M̵͝***

---

## The Solution

You write one file: `tendrl.toml`

### A highlight feed
``` toml
[feed.highlights]
name = "Highlights"
description = "Annotated excerpts - text that migrates between contexts"

relay_mode = "general"              # Use user's default relays
fetch_strategy = "stream"           # Real-time streaming
filter_by_follows = false           # Show all highlights

display_kinds = [9802]

[feed.highlights.root]
kind = 9802                         # Highlight events
template = "highlight-card"

# Author of the highlight
[feed.highlights.root.deps.author]
kind = 0
relation = "author"
required = true

# The event being highlighted (can be any kind)
[feed.highlights.root.deps.highlighted_event]
relation = "e_tag"
required = false
fetch_any_kind = true               # Don't restrict by kind

# Author of highlighted content
[feed.highlights.root.deps.highlighted_author]
kind = 0
relation = "highlighted_author"     # Custom relation
required = false

# Content authors (from p-tags with 'author' role)
[feed.highlights.root.deps.content_authors]
kind = 0
relation = "content_authors"
required = false
multiple = true                     # Can have multiple

# Stats on the highlight itself
[feed.highlights.root.deps.reactions]
kind = 7
relation = "e_tag"
mode = "aggregate"
stats = ["count", "by_content"]

[feed.highlights.root.deps.replies]
kind = 1
relation = "e_tag"
mode = "aggregate"
stats = ["count"]

[feed.highlights.root.deps.zaps]
kind = 9735
relation = "e_tag"
mode = "aggregate"
stats = ["count", "total_sats"]

```

You point an AI (Claude, GPT-4, whatever) at the target client's repository. You feed it the Tendrl system prompt. You wait.

The AI performs reconnaissance. It reads thousands of lines of code, learning:
- How the client fetches Nostr events from relays 🧠
- How it stores data in local databases
- How it renders UI components
- How it manages application state and routing
- How it handles user interactions and navigation

Then it generates injection code. Components that match the host's framework. Data fetching that uses the host's networking patterns. Database schemas that extend the existing structure. UI that renders in the host's exact style.

The code it writes **looks like the original developers wrote it.**

You copy the generated files into the client. The feeds appear. The client accepts them as its own.

---

## Philosophy 👁️

### Parasitic
Tendrl doesn't replace hosts. It i̶n̷f̶i̸l̸t̵r̶a̷t̸e̸s̴ them. The host client continues living, functioning normally, while Tendrl's modifications spread through the codebase. Users experience the new feeds as if they were always meant to be there.

### Amorphous  
Tendrl has no fixed form. It adapts to React, Vue, Svelte, Swift, Kotlin, whatever architecture it encounters. Each infection generates unique code. Each integration learns new patterns. 🌱

### Reckless
Tendrl doesn't wait for permission. You define what feeds you want. The AI finds injection points and generates the necessary code. No "official support" required. No API contracts. No stable interfaces.

### Config-Driven
You maintain only `tendrl.toml`. Everything else - components, data fetching, state management, UI integration - the AI generates by studying the host. When the host updates, you regenerate. The pattern adapts.

### AI-Powered
A sufficiently capable LLM analyzes the target codebase, reverse-engineers its patterns, and writes implementation code that mimics the host's style. You're not learning each client's architecture. The AI does. You're not writing adapter code. The AI does. 🦷

You just define what you want to grow.

---

## How It Works

### Phase 1: R̸͝e̴c̵o̷n̴n̸a̷i̵s̸s̴a̸n̷c̴e̸ 🫀

Feed the AI your target client's repository. Use the Tendrl system prompt (included in this repo). The AI will:

```
ANALYZING: component/FeedManager.tsx...
PATTERN DETECTED: Redux state management
NETWORKING: RTK Query with custom relay selector
RENDERING: Virtualized list with infinite scroll
STORAGE: IndexedDB via Dexie.js wrapper
INJECTION POINT IDENTIFIED: src/features/feeds/index.ts
```

It produces a profile of the host - a comprehensive map of how the client works. This isn't documentation. This is anatomical study. 🫁

### Phase 2: Adaptation 🧬

You provide your `tendrl.toml` configuration. The AI uses the host profile to generate implementation code:

- Components that perfectly match the host's framework and style
- Data fetching logic using the host's networking patterns  
- Database migrations that extend existing schemas
- State management code that integrates with the host's architecture
- Routing updates that feel intentional, not grafted on

The generated code doesn't look like foreign tissue. It looks **native**.

### Phase 3: Integration 🌿

You copy the generated files into the host client's codebase. You run the host's own build process. The new feeds appear in the interface, rendering with the host's own components, fetched through the host's own relay connections, stored in the host's own database.

The client never knew it was being modified. The feeds look like they were always planned.

---

## Feed Types

Once integrated, any feed type can be injected:

**Blogs** (NIP-23) - Long-form articles and blog posts  
**Highlights** (NIP-84) - Saved text selections and annotations  
**Zaps** (NIP-57) - Lightning payment activity streams 🦠  
**DVM Jobs** (NIP-90) - Data vending machine marketplace  
**Wiki Articles** (NIP-54) - Collaborative knowledge repositories  

Or anything else you define. The pattern is **amorphous**. It grows into whatever shape you need.

---

## Target Hosts

Successful modifications on
- **Flux** 
- **Gossip** 

---


## Nothing to Install 🦠

**There is only the pattern.**

1. Copy `tendrl.toml` (the configuration)
2. Copy `tendrl-system-prompt.md` (the AI instructions) 
3. Feed both to an AI with access to the target client's code
4. Receive generated injection code
5. Integrate into host
6. The feeds g̶r̷o̸w̵

---

## Known Limitations 🫀

Some architectures resist injection:

- **Heavily obfuscated code** - The AI needs readable patterns to learn from
- **Extreme type safety** - Languages like Rust with complex borrow checking
- **Closed-source clients** - Can't inject what you can't read
- **Clients without relay abstraction** - Direct relay coupling makes injection difficult
- **Hostile build systems** - Some tools actively prevent code modification

When injection fails, document the failure. The pattern learns from resistance.

---

## Warnings ⚠️

**Tendrl operates without permission.** You are modifying software that was not designed to be modified. The original developers did not consent to this integration. The host client's architecture may be damaged by improper injection.

**Generated code may contain vulnerabilities.** AI-written code can include security flaws, memory leaks, or incorrect error handling. Review all generated code before integration.

**The pattern spreads unpredictably.** Once you understand how to inject one feed, you'll want to inject more. Then inject them into more clients. Then teach others to inject. This is **intentional**. 🧬

**Updates may break everything.** When the host client updates, your injections may stop working. This is the cost of parasitism - you depend on the host's stability.


Use at your own risk. This is experimental software development through AI-mediated code generation.

We accept no responsibility for what grows.
Your prompts define the direction, its iterative.

---
## Serious Documentation

**→ [docs/TECHNICAL_README.md](docs/TECHNICAL_README.md)** - Actual architecture, build instructions, component specs.

---

## Source inspiration
Before I realized I could apply this to anything, I used
`nak` (fetching)+`sqlite` (databased)+`jinja2` (universal templating)
[nostr-feeds/flux](https://github.com/limina1/nostr-feeds)
## Barely Functional POCs
[flux publications](https://i.nostr.build/SIEj7OvHNyPuoC45.png)
[gossip zaps](https://i.nostr.build/xX867fY1BSIs2Oyd.png)
[gossip highlights](https://i.nostr.build/3StODL5isO2en3Z8.png)
[gossip publications](https://i.nostr.build/3OsI9erBnjIRLHqY.png)

## License

MIT - Because Tendrl should wrap around everything.
Fork it. Modify it. Let it m̴̀u̷͌t̶̚a̶̐t̷̊e̸̽.
Cite it, don't cite it. Tendrl always knows its roots 𖣂

---

---

*Built without permission* 🦷
*Literally no one asked for this*

**The pattern spreads. The pattern adapts. The pattern g̵r̶o̷w̸s̵.** 🌿🦠🧬

---

