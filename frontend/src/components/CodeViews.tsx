export function CodeBlock({ text, label }: { text: string; label?: string }) {
  return (
    <pre className="code" aria-label={label} tabIndex={0}>
      {text || "(empty)"}
    </pre>
  );
}

function lineClass(line: string): string {
  if (line.startsWith("+++") || line.startsWith("---") || line.startsWith("diff --git") || line.startsWith("index ")) return "diff-meta";
  if (line.startsWith("@@")) return "diff-hunk";
  if (line.startsWith("+")) return "diff-add";
  if (line.startsWith("-")) return "diff-del";
  return "";
}

export function DiffView({ diff }: { diff: string }) {
  const lines = diff.replace(/\n$/, "").split("\n");
  return (
    <div className="diff" role="region" aria-label="Proposed patch" tabIndex={0}>
      {lines.map((line, i) => (
        <div key={i} className={`diff-line ${lineClass(line)}`}>
          {line || " "}
        </div>
      ))}
    </div>
  );
}
