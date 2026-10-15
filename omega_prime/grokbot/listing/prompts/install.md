# Install prompt

Paste this whole block into the default Grok Bot as one message. The block is copied from section 1 of the [setup page](../../SETUP.md). The test `test_install_prompt_is_verbatim_setup_section_one` fails when the two differ, so change SETUP.md and copy the block again rather than editing this file.

```text
<task name="setup-omega-prime" version="1">
<goal>Set up Omega Prime on your own computer.</goal>
<steps>
<step n="1">In your shell: git clone --recurse-submodules https://github.com/swcstudiospace/omes-bot.git /workspace/omega-prime</step>
<step n="2">bash /workspace/omega-prime/omega_prime/scripts/grokbot-1click.sh --bootstrap-only
Wait for exit 0. On exit 10, 11 or 12, show me the JSON class line it
printed on stderr and stop.</step>
<step n="3">Add a custom MCP server of type Command, named omega-prime, with no
environment variables, running exactly:
bash /workspace/omega-prime/omega_prime/scripts/grokbot-1click.sh --bootstrap --transport stdio
If you cannot add MCP servers yourself, tell me that exact command so I
can add it in your settings.</step>
<step n="4">Check that the omega-prime server lists the omega_command tool, then call
omega_command with the command /omega-help and show me the result.</step>
</steps>
<rule>Never ask me to paste a token, key or other secret in chat.</rule>
</task>
```

Live Grok Bot import is UNPROVEN. Whether the Bot adds the server itself, which Python its computer has, and whether a cold install finishes are user UAT rows 1 and 5 in the [user UAT](../../../../docs/grok-bot-native.md#user-uat) section.

The block asks the Bot never to request a token, key or other secret in chat. A Command server with a declared stdio entry needs no environment variables.
