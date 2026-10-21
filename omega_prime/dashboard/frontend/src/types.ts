export type TimelineEntry = { at: string; state: string }
export type LinkRecord = { label: string; path_or_url: string }
export type Wiring = { phase: string | null; stage: string | null; repo: string | null; branch: string | null; head_sha: string | null; worktree?: string | null }
export type HerdrPane = { pane_id: string; workspace?: string }
export type Task = {
  id: string; title: string; source: string; owner?: { kind?: string; ref?: string; label?: string } | null
  state: string; started_at?: string | null; last_activity_at?: string | null
  progress?: { done: number; total: number } | null; model?: string | null; detail?: string | null
  links?: LinkRecord[]; seq?: number | null; display_id?: string | null; timeline?: TimelineEntry[]; wiring?: Wiring | null
  plan?: string | null; phase?: string | null; stage?: string | null; worktree?: string | null; herdr_pane?: HerdrPane | null
}
export type WaitingItem = { id: string; ask: string; since?: string | null; blocks?: string; seq?: number | null; display_id?: string | null; timeline?: TimelineEntry[]; related_task_id?: string | null }
export type FinishedItem = { id: string; title: string; finished_at?: string | null; result: string; receipt?: string | null; summary?: string | null; seq?: number | null; display_id?: string | null; timeline?: TimelineEntry[]; wiring?: Wiring | null; state?: string | null; source?: string | null; owner?: Task['owner']; model?: string | null; detail?: string | null; links?: LinkRecord[]; started_at?: string | null; last_activity_at?: string | null; progress?: Task['progress']; plan?: string | null; phase?: string | null; stage?: string | null; worktree?: string | null; herdr_pane?: HerdrPane | null }
export type PrRow = { id: string; repo: string; number: number; title: string; branch: string; draft: boolean; mergeable: boolean | null; ci: string; greptile: string; merge_rule: string; updated_at: string | null }
export type Check = { id?: string; name: string; status: string; conclusion?: string | null; started_at?: string | null; completed_at?: string | null; duration_s?: number | null; details_url?: string | null; head_sha?: string | null }
export type CiRow = { id: string; kind: 'pull_request' | 'branch' | string; repo?: string | null; number?: number | null; title?: string | null; url?: string | null; branch?: string | null; base_branch?: string | null; head_sha?: string | null; draft?: boolean; mergeable?: boolean | null; ci?: string | null; checks?: Check[]; greptile_review?: { score?: number | null; reviewed_sha?: string | null; source?: string | null; submitted_at?: string | null; url?: string | null; matches_head?: boolean | null } | null }
export type Repository = { id: number | string; full_name: string; name: string; url?: string | null; visibility?: string | null; default_branch?: string | null; pushed_at?: string | null; language?: string | null; open_pr_count?: number | null; latest_main_ci?: { branch?: string; head_sha?: string; ci?: string; checks?: Check[] } | null }
export type SectionMeta = { fetched_at?: string | null; stale?: boolean; partial?: boolean; reason?: string | null }
export type GitHubMeta = { fetched_at?: string | null; last_attempt_at?: string | null; cache_ttl_s?: number; stale?: boolean; partial?: boolean; reason?: string | null; errors?: string[]; truncated?: string[]; sections?: Record<string, SectionMeta> }
export type CommandCenter = { repositories?: Repository[] | null; ci_cd?: CiRow[] | null; recent_merges?: Array<{ id: string; repo?: string; number?: number; title?: string; url?: string; merged_at?: string; merge_commit_sha?: string | null }> | null; meta?: GitHubMeta | null }
export type Source = { status?: string; error?: string | null; detail?: string | null; items?: unknown[]; command_center?: CommandCenter }
export type Snapshot = { generated_at?: string | null; sources?: Record<string, Source>; active?: Task[]; idle_sessions?: Task[]; pr_gate?: PrRow[]; waiting_on_ove?: WaitingItem[]; finished?: FinishedItem[] }
