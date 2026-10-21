# Configuration inventory

This inventory covers names proven from production Python source only.
It does not cover shell, YAML, interpolation-only inputs, arbitrary plugins, or embedded child-program code.
A listed name does not imply that the setting is enabled or configured.
The table records names and purposes only.

| Variable | Purpose |
| --- | --- |
| `AI_GATEWAY_API_KEY` | Authenticates provider requests routed through AI Gateway. |
| `AI_GATEWAY_API_KEY_FILE` | Names a protected file supplying AI_GATEWAY_API_KEY: Authenticates provider requests routed through AI Gateway. |
| `ANTHROPIC_API_KEY` | Authenticates Anthropic API requests. |
| `ANTHROPIC_API_KEY_FILE` | Names a protected file containing the Anthropic API credential. |
| `ANTHROPIC_OAUTH_TOKEN` | Authenticates Anthropic requests using OAuth. |
| `ANTHROPIC_OAUTH_TOKEN_FILE` | Names a protected file supplying ANTHROPIC_OAUTH_TOKEN: Authenticates Anthropic requests using OAuth. |
| `ASC_ISSUER_ID` | Identifies the App Store Connect API issuer. |
| `ASC_ISSUER_ID_FILE` | Names a protected file containing the App Store Connect issuer identifier. |
| `ASC_KEY_ID` | Identifies the App Store Connect API key. |
| `ASC_KEY_ID_FILE` | Names a protected file containing the App Store Connect key identifier. |
| `ASC_PRIVATE_KEY` | Provides the App Store Connect signing private key. |
| `ASC_PRIVATE_KEY_FILE` | Names a protected file containing the App Store Connect signing key. |
| `AZURE_OPENAI_API_KEY` | Authenticates Azure OpenAI API requests. |
| `AZURE_OPENAI_API_KEY_FILE` | Names a protected file supplying AZURE_OPENAI_API_KEY: Authenticates Azure OpenAI API requests. |
| `CEREBRAS_API_KEY` | Authenticates Cerebras API requests. |
| `CEREBRAS_API_KEY_FILE` | Names a protected file supplying CEREBRAS_API_KEY: Authenticates Cerebras API requests. |
| `CLOUDFLARE_API_KEY` | Authenticates Cloudflare AI provider requests. |
| `CLOUDFLARE_API_KEY_FILE` | Names a protected file supplying CLOUDFLARE_API_KEY: Authenticates Cloudflare AI provider requests. |
| `COPILOT_GITHUB_TOKEN` | Authenticates GitHub Copilot provider requests. |
| `COPILOT_GITHUB_TOKEN_FILE` | Names a protected file supplying COPILOT_GITHUB_TOKEN: Authenticates GitHub Copilot provider requests. |
| `CURSOR_API_KEY` | Authenticates Cursor provider requests. |
| `CURSOR_API_KEY_FILE` | Names a protected file containing the Cursor provider credential. |
| `DEEPSEEK_API_KEY` | Authenticates DeepSeek API requests. |
| `DEEPSEEK_API_KEY_FILE` | Names a protected file supplying DEEPSEEK_API_KEY: Authenticates DeepSeek API requests. |
| `DISCORD_BOT_TOKEN` | Authenticates the Discord connector. |
| `DISCORD_BOT_TOKEN_FILE` | Names a protected file containing the Discord connector credential. |
| `FIREWORKS_API_KEY` | Authenticates Fireworks API requests. |
| `FIREWORKS_API_KEY_FILE` | Names a protected file supplying FIREWORKS_API_KEY: Authenticates Fireworks API requests. |
| `FLEET_COORDINATOR_TOKEN` | Authenticates requests to the Fleet coordinator. |
| `FLEET_COORDINATOR_TOKEN_FILE` | Names a protected file containing the Fleet coordinator credential. |
| `FLEET_COORDINATOR_URL` | Selects the approved Fleet coordinator endpoint. |
| `FLEET_COORDINATOR_URL_FILE` | Names a protected file supplying FLEET_COORDINATOR_URL: Selects the approved Fleet coordinator endpoint. |
| `GEMINI_API_KEY` | Authenticates Gemini API requests. |
| `GEMINI_API_KEY_FILE` | Names a protected file containing the Gemini API credential. |
| `GH_TOKEN` | Authenticates GitHub CLI and GitHub integrations. |
| `GH_TOKEN_FILE` | Names a protected file supplying GH_TOKEN: Authenticates GitHub CLI and GitHub integrations. |
| `GITHUB_SHA` | Records the source commit identifier as evaluation-history provenance. |
| `GITHUB_TOKEN` | Authenticates GitHub API requests. |
| `GITHUB_TOKEN_FILE` | Names a protected file supplying GITHUB_TOKEN: Authenticates GitHub API requests. |
| `GOOGLE_API_KEY` | Authenticates Google provider requests. |
| `GOOGLE_API_KEY_FILE` | Names a protected file containing the Google API credential. |
| `GOOGLE_CLOUD_API_KEY` | Authenticates Google Cloud provider requests. |
| `GOOGLE_CLOUD_API_KEY_FILE` | Names a protected file supplying GOOGLE_CLOUD_API_KEY: Authenticates Google Cloud provider requests. |
| `GREPTILE_API_KEY` | Authenticates Greptile API requests. |
| `GREPTILE_API_KEY_FILE` | Names a protected file containing the Greptile credential. |
| `GREPTILE_GITHUB_TOKEN` | Authenticates Greptile access to GitHub. |
| `GREPTILE_GITHUB_TOKEN_FILE` | Names a protected file containing the Greptile GitHub credential. |
| `GREPTILE_MCP_URL` | Selects the explicitly trusted Greptile MCP endpoint. |
| `GROK_HOME` | Selects the Grok integration home directory. |
| `GROK_PLUGIN_DATA` | Selects the Grok plugin data directory. |
| `GROQ_API_KEY` | Authenticates Groq API requests. |
| `GROQ_API_KEY_FILE` | Names a protected file supplying GROQ_API_KEY: Authenticates Groq API requests. |
| `HERMES_HOME` | Selects the Hermes integration home directory. |
| `HF_TOKEN` | Authenticates Hugging Face provider requests. |
| `HF_TOKEN_FILE` | Names a protected file supplying HF_TOKEN: Authenticates Hugging Face provider requests. |
| `HINDSIGHT_API_KEY` | Authenticates Hindsight API requests. |
| `HINDSIGHT_API_KEY_FILE` | Names a protected file containing the Hindsight API credential. |
| `HINDSIGHT_API_TOKEN` | Authenticates Hindsight API requests using a token. |
| `HINDSIGHT_API_TOKEN_FILE` | Names a protected file containing the Hindsight token. |
| `HINDSIGHT_URL` | Selects the Hindsight service endpoint. |
| `HINDSIGHT_URL_FILE` | Names a protected file containing the Hindsight endpoint value. |
| `HOME` | Provides the host home directory used by child processes. |
| `HOME_FILE` | Forwarded file-name counterpart for the child-process HOME setting. |
| `KIMI_API_KEY` | Authenticates Kimi provider requests. |
| `KIMI_API_KEY_FILE` | Names a protected file supplying KIMI_API_KEY: Authenticates Kimi provider requests. |
| `LANG` | Selects the locale inherited by child processes. |
| `LANG_FILE` | Forwarded file-name counterpart for the child-process locale setting. |
| `LC_ALL` | Selects the all-categories locale inherited by child processes. |
| `LC_ALL_FILE` | Forwarded file-name counterpart for the child-process locale setting. |
| `LC_CTYPE` | Selects the character locale inherited by child processes. |
| `LC_CTYPE_FILE` | Forwarded file-name counterpart for the child-process character locale. |
| `LINEAR_API_KEY` | Authenticates Linear API requests for connector setup and issue operations. |
| `LINEAR_API_KEY_FILE` | Names a protected file containing the Linear API credential. |
| `LOGNAME` | Identifies the host user in child processes. |
| `LOGNAME_FILE` | Forwarded file-name counterpart for the child-process login name. |
| `MCP_AUTH_TOKEN` | Authenticates bearer requests to the MCP tool server. |
| `META_API_KEY` | Authenticates Meta provider requests. |
| `META_API_KEY_FILE` | Names a protected file containing the Meta provider credential. |
| `MINIMAX_API_KEY` | Authenticates MiniMax API requests. |
| `MINIMAX_API_KEY_FILE` | Names a protected file supplying MINIMAX_API_KEY: Authenticates MiniMax API requests. |
| `MINIMAX_CN_API_KEY` | Authenticates MiniMax China API requests. |
| `MINIMAX_CN_API_KEY_FILE` | Names a protected file supplying MINIMAX_CN_API_KEY: Authenticates MiniMax China API requests. |
| `MISTRAL_API_KEY` | Authenticates Mistral API requests. |
| `MISTRAL_API_KEY_FILE` | Names a protected file supplying MISTRAL_API_KEY: Authenticates Mistral API requests. |
| `MOONSHOT_API_KEY` | Authenticates Moonshot API requests. |
| `MOONSHOT_API_KEY_FILE` | Names a protected file supplying MOONSHOT_API_KEY: Authenticates Moonshot API requests. |
| `MUSE_API_KEY` | Authenticates Muse provider requests. |
| `MUSE_API_KEY_FILE` | Names a protected file containing the Muse provider credential. |
| `NOTION_TOKEN` | Authenticates Notion connector API requests. |
| `NOTION_TOKEN_FILE` | Names a protected file containing the Notion integration credential. |
| `OMEGA_PRIME_AGENT_STATE_DIR` | Selects the agent data folder for sessions, memory, skills and intake; defaults to the state directory's agent folder. |
| `OMEGA_PRIME_APPROVALS_FILE` | Selects the external approval grants file. |
| `OMEGA_PRIME_APPROVALS_SHA256` | Pins the expected digest of the approval grants file. |
| `OMEGA_PRIME_AUDIT_LOG` | Selects the destination for Omega Prime audit events. |
| `OMEGA_PRIME_BWRAP_BIN` | Selects the bubblewrap executable used for sandboxing. |
| `OMEGA_PRIME_CLARIFY_SOCKET` | Names the per-run clarify broker socket an engine child uses to ask the operator a question. |
| `OMEGA_PRIME_COMMAND_LOG` | Selects the command history log destination. |
| `OMEGA_PRIME_CRON_LATENESS_GRACE_SECONDS` | Sets the maximum accepted lateness for scheduled actions. |
| `OMEGA_PRIME_DELEGATE_DEPTH` | Sets the delegate depth an engine child's MCP server starts at, so nested delegates keep the parent's depth limit; set by the engine, not by operators. |
| `OMEGA_PRIME_DESK_BUS_URL` | Selects the desk message-bus endpoint. |
| `OMEGA_PRIME_DESK_BUS_URL_FILE` | Names a protected file containing the desk message-bus endpoint. |
| `OMEGA_PRIME_DESK_CHILD_MODEL` | Selects the model used for the managed desk child process. |
| `OMEGA_PRIME_DESK_CHILD_MODEL_FILE` | Names a protected file containing the desk child model setting. |
| `OMEGA_PRIME_DESK_CHILD_PROVIDER` | Selects the provider used for the managed desk child process. |
| `OMEGA_PRIME_DESK_CHILD_PROVIDER_FILE` | Names a protected file containing the desk child provider setting. |
| `OMEGA_PRIME_DESK_DOCS_INDEX` | Selects the desk documentation index endpoint. |
| `OMEGA_PRIME_DESK_DOCS_INDEX_FILE` | Names a protected file containing the desk documentation endpoint. |
| `OMEGA_PRIME_DESK_NOTIFY_URL` | Selects the desk notification endpoint. |
| `OMEGA_PRIME_DESK_NOTIFY_URL_FILE` | Names a protected file containing the desk notification endpoint. |
| `OMEGA_PRIME_DISPATCH_CONFIRM` | Confirms dispatch operations requiring the Fleet credential helper. |
| `OMEGA_PRIME_DISPATCH_CONFIRM_FILE` | Names a protected file containing the dispatch confirmation token. |
| `OMEGA_PRIME_ENGINE` | Controls whether engine execution is enabled or required. |
| `OMEGA_PRIME_ENGINE_STAGE` | Names the GSD stage (plan or execute) an engine run is on, so staged turns and their delegates pick the stage model. |
| `OMEGA_PRIME_ENGINE_TMUX` | Controls tmux session attachment for engine jobs. |
| `OMEGA_PRIME_ENGINE_UNSANDBOXED` | Opts engine execution out of the sandbox. |
| `OMEGA_PRIME_EVALS_JUDGE_API_KEY` | Authenticates requests to the live evaluation judge. |
| `OMEGA_PRIME_EVALS_JUDGE_ENABLED` | Enables the live-model evaluation judge. |
| `OMEGA_PRIME_EVALS_JUDGE_ENDPOINT` | Selects the live evaluation judge endpoint. |
| `OMEGA_PRIME_EVALS_JUDGE_MODEL` | Selects the model used by the live evaluation judge. |
| `OMEGA_PRIME_EVALS_REGRESS_THRESHOLD` | Sets the minimum per-case score drop counted as an evaluation regression. |
| `OMEGA_PRIME_IMAGE_ROLLOUT` | Selects the staged or direct image rollout mode for deployments. |
| `OMEGA_PRIME_MEMORY_BACKEND` | Selects which memory backend serves recall and writes. |
| `OMEGA_PRIME_MEMORY_DIR` | Selects the local memory store directory. |
| `OMEGA_PRIME_OMP_ALLOW_CHATGPT` | Enables the ChatGPT plan in engine model routing. |
| `OMEGA_PRIME_OMP_ALLOW_CURSOR` | Enables the Cursor plan in engine model routing. |
| `OMEGA_PRIME_OMP_ALLOW_XAI` | Enables the xAI plan in engine model routing. |
| `OMEGA_PRIME_OMP_ALLOW_ZAI` | Enables the Z.ai plan in engine model routing. |
| `OMEGA_PRIME_OMP_BACKEND` | Selects the omp engine backend; omp-sandbox routes engine stages through the omp-sandbox wrapper instead of bwrap. |
| `OMEGA_PRIME_OMP_BIN` | Selects the omp executable used by the engine. |
| `OMEGA_PRIME_OMP_HOME` | Selects the omp configuration home directory. |
| `OMEGA_PRIME_OMP_MODEL_ANTHROPIC` | Overrides the default model for the Anthropic engine plan. |
| `OMEGA_PRIME_OMP_MODEL_ANTHROPIC_EXECUTE` | Overrides the execution-stage Anthropic model. |
| `OMEGA_PRIME_OMP_MODEL_ANTHROPIC_PLAN` | Overrides the planning-stage Anthropic model. |
| `OMEGA_PRIME_OMP_MODEL_CHATGPT` | Overrides the default model for the ChatGPT engine plan. |
| `OMEGA_PRIME_OMP_MODEL_CHATGPT_EXECUTE` | Overrides the execution-stage ChatGPT model. |
| `OMEGA_PRIME_OMP_MODEL_CHATGPT_PLAN` | Overrides the planning-stage ChatGPT model. |
| `OMEGA_PRIME_OMP_MODEL_CURSOR` | Overrides the default model for the Cursor engine plan. |
| `OMEGA_PRIME_OMP_MODEL_CURSOR_EXECUTE` | Overrides the execution-stage Cursor model. |
| `OMEGA_PRIME_OMP_MODEL_CURSOR_PLAN` | Overrides the planning-stage Cursor model. |
| `OMEGA_PRIME_OMP_MODEL_MUSE` | Overrides the default model for the Muse engine plan. |
| `OMEGA_PRIME_OMP_MODEL_MUSE_EXECUTE` | Overrides the execution-stage Muse model. |
| `OMEGA_PRIME_OMP_MODEL_MUSE_PLAN` | Overrides the planning-stage Muse model. |
| `OMEGA_PRIME_OMP_MODEL_XAI` | Overrides the default model for the xAI engine plan. |
| `OMEGA_PRIME_OMP_MODEL_XAI_EXECUTE` | Overrides the execution-stage xAI model. |
| `OMEGA_PRIME_OMP_MODEL_XAI_PLAN` | Overrides the planning-stage xAI model. |
| `OMEGA_PRIME_OMP_MODEL_ZAI` | Overrides the default model for the Z.ai engine plan. |
| `OMEGA_PRIME_OMP_MODEL_ZAI_EXECUTE` | Overrides the execution-stage Z.ai model. |
| `OMEGA_PRIME_OMP_MODEL_ZAI_PLAN` | Overrides the planning-stage Z.ai model. |
| `OMEGA_PRIME_OMP_SANDBOX_ANDA` | Selects the omp-sandbox-anda wrapper executable used by the omp-sandbox engine backend. |
| `OMEGA_PRIME_PACKS_JSON` | Selects the configured tool-pack description file. |
| `OMEGA_PRIME_PACKS_JSON_FILE` | Names a protected file containing the tool-pack description setting. |
| `OMEGA_PRIME_PACK_API_BASES_JSON` | Selects the tool-pack API base configuration. |
| `OMEGA_PRIME_PACK_API_BASES_JSON_FILE` | Names a protected file containing the tool-pack API base configuration. |
| `OMEGA_PRIME_PLUGINS` | Opts in to loading trusted local Omega Prime plugins when set exactly to 1. |
| `OMEGA_PRIME_PRIME_AUTONOMOUS_ENABLED` | Enables the autonomous Prime capability family. |
| `OMEGA_PRIME_PRIME_GOALS_ENABLED` | Enables the goals Prime capability family. |
| `OMEGA_PRIME_PRIME_HARNESS_ENABLED` | Enables the harness Prime capability family. |
| `OMEGA_PRIME_PRIME_HEARTBEAT_ENABLED` | Enables the heartbeat Prime capability family. |
| `OMEGA_PRIME_PRIME_KERNEL_ENABLED` | Enables the kernel Prime capability family. |
| `OMEGA_PRIME_PRIME_MESSAGING_ENABLED` | Enables the messaging Prime capability family. |
| `OMEGA_PRIME_PRIME_RLM_ENABLED` | Enables the RLM Prime capability family. |
| `OMEGA_PRIME_QUOTAS_DOCUMENT` | Selects the quota configuration document. |
| `OMEGA_PRIME_QUOTAS_MODE` | Selects report, soft, or hard quota enforcement behavior. |
| `OMEGA_PRIME_RAILWAY_PROJECTS` | Restricts Railway status and log operations to named projects. |
| `OMEGA_PRIME_RAILWAY_PROJECTS_FILE` | Names a protected file containing the Railway project restriction setting. |
| `OMEGA_PRIME_REDTEAM_AUTO_PIN_BUMP` | Enables release-watch initiated pin-bump processing. |
| `OMEGA_PRIME_REDTEAM_COVERAGE_ALERT_THRESHOLD` | Sets the red-team pass-rate threshold that triggers a coverage alert. |
| `OMEGA_PRIME_REDTEAM_ENABLED` | Enables the red-team capability. |
| `OMEGA_PRIME_REDTEAM_FINDINGS_SYNC_ENABLED` | Enables red-team findings synchronization to a tracker. |
| `OMEGA_PRIME_REDTEAM_FINDINGS_SYNC_LINEAR_TEAM_ID` | Selects the Linear team receiving red-team findings. |
| `OMEGA_PRIME_REDTEAM_FINDINGS_SYNC_REPO` | Selects the repository receiving synchronized red-team findings. |
| `OMEGA_PRIME_REDTEAM_FINDINGS_SYNC_SLACK_WEBHOOK_URL` | Selects the Slack webhook for findings synchronization. |
| `OMEGA_PRIME_REDTEAM_FINDINGS_SYNC_TEAMS_WEBHOOK_URL` | Selects the Teams webhook for findings synchronization. |
| `OMEGA_PRIME_REDTEAM_FINDINGS_SYNC_TRACKER` | Selects the tracker backend for red-team findings synchronization. |
| `OMEGA_PRIME_REDTEAM_FINDINGS_SYNC_WEBHOOK_SIGNING_SECRET` | Provides the active HMAC key for generic red-team webhook signatures. |
| `OMEGA_PRIME_REDTEAM_FINDINGS_SYNC_WEBHOOK_SIGNING_SECRET_PREVIOUS` | Provides the prior HMAC key during webhook signing-key rotation. |
| `OMEGA_PRIME_REDTEAM_FINDINGS_SYNC_WEBHOOK_URL` | Selects the generic webhook endpoint for findings synchronization. |
| `OMEGA_PRIME_REDTEAM_GATEWAY_URL` | Selects the red-team gateway endpoint. |
| `OMEGA_PRIME_REDTEAM_KILL` | Activates the red-team kill switch. |
| `OMEGA_PRIME_REDTEAM_REPORTS_DIR` | Selects the red-team engagement report output directory. |
| `OMEGA_PRIME_REDTEAM_ROE_FILE` | Selects the signed red-team Rules of Engagement artifact. |
| `OMEGA_PRIME_REDTEAM_ROE_FILES` | Selects comma-separated red-team Rules of Engagement artifacts. |
| `OMEGA_PRIME_REDTEAM_ROE_KEY` | Provides the detached HMAC key for Rules of Engagement verification. |
| `OMEGA_PRIME_REDTEAM_ROE_KEY_FILE` | Selects a file containing the Rules of Engagement verification key. |
| `OMEGA_PRIME_REDTEAM_ROE_REQUIRED` | Requires Rules of Engagement authorization for red-team operations. |
| `OMEGA_PRIME_ROTATION_ENABLED` | Enables scheduled credential rotation. |
| `OMEGA_PRIME_ROTATION_OVERLAP_HOURS` | Sets the overlap period for scheduled credential rotation. |
| `OMEGA_PRIME_SANDBOX_ENABLED` | Enables sandboxed execution. |
| `OMEGA_PRIME_SANDBOX_POLICY` | Selects the generated sandbox policy file. |
| `OMEGA_PRIME_SANDBOX_SCOPE` | Selects which execution phases the sandbox governs. |
| `OMEGA_PRIME_SIDECAR_REPORTS_DIR` | Selects the red-team sidecar reports directory. |
| `OMEGA_PRIME_SOLO` | Turns solo mode on or off for the MCP tool registry; defaults on for the Omega Prime bot. |
| `OMEGA_PRIME_STATE_DIR` | Selects persistent state for Omega Prime integrations and engine jobs. |
| `OMEGA_PRIME_STATE_DIR_FILE` | Names a protected file containing the Omega Prime state directory setting. |
| `OMEGA_PRIME_SWEEP_MENTIONS` | Controls mention processing during scheduled sweeps. |
| `OMEGA_PRIME_TELEMETRY_OTLP_ENABLED` | Enables OpenTelemetry OTLP export. |
| `OMEGA_PRIME_TELEMETRY_OTLP_ENDPOINT` | Selects the OTLP telemetry push endpoint. |
| `OMEGA_PRIME_TELEMETRY_OTLP_INTERVAL_SECONDS` | Sets the OTLP telemetry export interval. |
| `OMEGA_PRIME_WORK_ROOT` | Selects the workspace root used by Omega Prime operations. |
| `OMEGA_PRIME_WORK_ROOT_FILE` | Names a protected file containing the Omega Prime workspace root. |
| `OMP_SANDBOX_ROOT` | Selects the omp-sandbox package root the wrapper starts its TS core from. |
| `OMP_SANDBOX_WORKSPACE_ROOTS` | Lists the allowlisted roots a job worktree may live under for the omp-sandbox backend. |
| `OPENAI_API_KEY` | Authenticates OpenAI API requests. |
| `OPENAI_API_KEY_FILE` | Names a protected file containing the OpenAI API credential. |
| `OPENCODE_API_KEY` | Authenticates OpenCode provider requests. |
| `OPENCODE_API_KEY_FILE` | Names a protected file supplying OPENCODE_API_KEY: Authenticates OpenCode provider requests. |
| `OPENROUTER_API_KEY` | Authenticates OpenRouter API requests. |
| `OPENROUTER_API_KEY_FILE` | Names a protected file supplying OPENROUTER_API_KEY: Authenticates OpenRouter API requests. |
| `P46_TEST_DATA` | Selects a temporary directory for the browser egress resolver-profile verification. |
| `PATH` | Provides executable search paths to child processes. |
| `PATH_FILE` | Forwarded file-name counterpart for the child-process executable search path. |
| `PI_CODING_AGENT_DIR` | Sets the pi coding agent data directory in the managed child environment. |
| `PLAY_CONSOLE_TOKEN` | Authenticates Google Play Console operations. |
| `PLAY_CONSOLE_TOKEN_FILE` | Names a protected file containing the Play Console credential. |
| `PRIME_API_KEY` | Authenticates Prime provider requests. |
| `PRIME_API_KEY_FILE` | Names a protected file supplying PRIME_API_KEY: Authenticates Prime provider requests. |
| `PYTHONHOME` | Selects the Python runtime prefix inherited by child processes. |
| `PYTHONHOME_FILE` | Forwarded file-name counterpart for the child-process Python home. |
| `PYTHONPATH` | Sets import search paths inherited by child processes. |
| `PYTHONPATH_FILE` | Forwarded file-name counterpart for the child-process Python import path. |
| `RAILWAY_TOKEN` | Authenticates Railway project status and log operations. |
| `RAILWAY_TOKEN_FILE` | Names a protected file containing the Railway credential. |
| `SUBSTRATE_TOKEN` | Authenticates requests to the Substrate service. |
| `SUBSTRATE_TOKEN_FILE` | Names a protected file containing the Substrate credential. |
| `SUBSTRATE_TOKEN_GROK_BOT` | Authenticates Grokbot requests to the Substrate service. |
| `SUBSTRATE_TOKEN_GROK_BOT_FILE` | Names a protected file containing the Grokbot Substrate credential. |
| `SUBSTRATE_URL` | Selects the Substrate service endpoint. |
| `SUBSTRATE_URL_FILE` | Names a protected file containing the Substrate endpoint setting. |
| `T3MP3ST_BEARER_TOKEN` | Authenticates requests to the T3MP3ST gateway. |
| `T3MP3ST_BEARER_TOKEN_FILE` | Names a protected file containing the T3MP3ST gateway credential. |
| `TELEGRAM_BOT_TOKEN` | Authenticates the Telegram connector. |
| `TELEGRAM_BOT_TOKEN_FILE` | Names a protected file containing the Telegram credential. |
| `TERM` | Selects the terminal type inherited by child processes. |
| `TERM_FILE` | Forwarded file-name counterpart for the child-process terminal type. |
| `TMPDIR` | Selects the temporary directory inherited by child processes. |
| `TMPDIR_FILE` | Forwarded file-name counterpart for the child-process temporary directory. |
| `ULTRATHINK_ROOT` | Selects the ultrathink integration root directory. |
| `ULTRATHINK_ROOT_FILE` | Names a protected file containing the ultrathink integration root. |
| `ULTRATHINK_SHIP` | Enables ultrathink ship writes only for the exact values 1 and true. |
| `ULTRATHINK_STATE_DIR` | Selects persistent state for the ultrathink integration. |
| `USER` | Identifies the host user inherited by child processes. |
| `USER_FILE` | Forwarded file-name counterpart for the child-process user name. |
| `VERCEL_TOKEN` | Authenticates Vercel operations. |
| `VERCEL_TOKEN_FILE` | Names a protected file containing the Vercel credential. |
| `VIRTUAL_ENV` | Selects the Python virtual environment inherited by child processes. |
| `VIRTUAL_ENV_FILE` | Forwarded file-name counterpart for the child-process virtual environment. |
| `XAI_API_KEY` | Authenticates xAI API requests. |
| `XAI_API_KEY_FILE` | Names a protected file containing the xAI provider credential. |
| `XDG_CONFIG_HOME` | Selects the XDG configuration directory. |
| `XDG_RUNTIME_DIR` | Selects the XDG runtime directory used by host processes. |
| `XDG_RUNTIME_DIR_FILE` | Forwarded file-name counterpart for the XDG runtime directory. |
| `XDG_STATE_HOME` | Selects the XDG state directory. |
| `XIAOMI_API_KEY` | Authenticates Xiaomi API requests. |
| `XIAOMI_API_KEY_FILE` | Names a protected file supplying XIAOMI_API_KEY: Authenticates Xiaomi API requests. |
| `XIAOMI_TOKEN_PLAN_AMS_API_KEY` | Authenticates Xiaomi Amsterdam token-plan requests. |
| `XIAOMI_TOKEN_PLAN_AMS_API_KEY_FILE` | Names a protected file supplying XIAOMI_TOKEN_PLAN_AMS_API_KEY: Authenticates Xiaomi Amsterdam token-plan requests. |
| `XIAOMI_TOKEN_PLAN_CN_API_KEY` | Authenticates Xiaomi China token-plan requests. |
| `XIAOMI_TOKEN_PLAN_CN_API_KEY_FILE` | Names a protected file supplying XIAOMI_TOKEN_PLAN_CN_API_KEY: Authenticates Xiaomi China token-plan requests. |
| `XIAOMI_TOKEN_PLAN_SGP_API_KEY` | Authenticates Xiaomi Singapore token-plan requests. |
| `XIAOMI_TOKEN_PLAN_SGP_API_KEY_FILE` | Names a protected file supplying XIAOMI_TOKEN_PLAN_SGP_API_KEY: Authenticates Xiaomi Singapore token-plan requests. |
| `X_API_TOKEN` | Authenticates the X connector. |
| `X_API_TOKEN_FILE` | Names a protected file containing the X connector credential. |
| `ZAI_API_KEY` | Authenticates Z.ai provider requests. |
| `ZAI_API_KEY_FILE` | Names a protected file containing the Z.ai provider credential. |
