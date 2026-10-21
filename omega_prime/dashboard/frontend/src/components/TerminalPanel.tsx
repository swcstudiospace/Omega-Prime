export function TerminalPanel({ taskId }: { taskId: string }) {
  return (
    <section aria-label={`Terminal for ${taskId}`}>
      <p>Terminal streaming not connected</p>
    </section>
  );
}
