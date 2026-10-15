# Notion Task Management Dashboard

This file is the template. Omega Prime does not publish a Notion gallery
listing, and it does not ship a workspace id. Duplicate the shape below
into your own Notion, then share that database with your integration.

Contract version: 1.

## Database

One database, named Tasks.

| Property | Type | Required | Notes |
| --- | --- | --- | --- |
| Name | title | yes | The task title. |
| Status | status | yes | Options, exactly: Not started, Up next, In progress, Done. |
| Due date | date | no | Omit the property or keep this type. |
| Project | relation | no | Omit the property or keep this type. A wrong type is a mismatch. |

The bot reads and writes only a database whose live schema matches this
table. It does not create a second database and it does not follow links
from chat into other Notion content.

## Setup

1. Create the database in your Notion workspace.
2. Create an internal integration with read content, update content, and
   insert content. Those are the only scopes the bot uses. `task add`
   creates a page, which Notion calls Insert content.
3. Share the Tasks database with that integration.
4. On the host, set `NOTION_TOKEN`, or put the token in a mode 0600 file
   and set `NOTION_TOKEN_FILE` to that path. Do not paste the token into
   chat.
5. In the bot, send `/omega-connectors`. The first reply is this template.
   Reply `/omega-connectors duplicated`, then `/omega-connectors connected`.

A missing required property stays unready and names the gap. Fix the
duplicate and reply `/omega-connectors connected` again. That does not
create a second binding.

## Linear, after Notion is ready

Set `LINEAR_API_KEY`, or a mode 0600 file named by `LINEAR_API_KEY_FILE`.
The key needs to read teams and create issues. Reply
`/omega-connectors connected`. When the account has more than one team or
project, the bot lists them and waits for
`/omega-connectors linear-team <id>` or
`/omega-connectors linear-project <id>`.

## Optional gateways

After Linear is ready the bot asks about three existing optional
endpoints: substrate, the red-team gateway, and the desk bus. Reply
`/omega-connectors skip-gateway` to leave them unset. Notion and Linear
keep working. A later gateway command tells you it is not configured.

`/omega-connectors gateway substrate <url>` (or `redteam`, or `desk_bus`)
validates that base URL. The probe requests `/healthz` on substrate and
`/health` on redteam and the desk bus, with the same bearer the runtime
client would send. Use http or https, with no username or password in
the URL. Connection refused, TLS failure, auth failure, a server error,
and timeout are reported separately. Auth failure marks that gateway
invalid. A server error or timeout marks it degraded and keeps the URL
for a later retry. A later replacement that fails leaves a working URL
in place.
