/**
 * Ctrl/Cmd-K to jump to any tool.
 *
 * Grouping the tools made them navigable; this makes them reachable. Someone
 * who already knows they want "Redact" should not have to remember which
 * group it lives in, and with two dozen tools the fastest route to any of
 * them is typing its name.
 */
import { useEffect, useMemo, useRef, useState } from "react";

export interface Command {
  id: string;
  label: string;
  group: string;
  hint?: string;
}

interface Props {
  commands: Command[];
  onRun: (id: string) => void;
  onClose: () => void;
}

/** Rank by where the match falls: a prefix beats a word start beats anywhere. */
function score(command: Command, query: string): number {
  const label = command.label.toLowerCase();
  const group = command.group.toLowerCase();
  const hint = (command.hint ?? "").toLowerCase();

  if (label.startsWith(query)) return 0;
  if (label.includes(query)) return 1;
  if (group.startsWith(query)) return 2;
  if (hint.includes(query)) return 3;
  return Number.POSITIVE_INFINITY;
}

export function CommandPalette({ commands, onRun, onClose }: Props) {
  const [query, setQuery] = useState("");
  const [cursor, setCursor] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => { inputRef.current?.focus(); }, []);

  const matches = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return commands;
    return commands
      .map((command) => ({ command, rank: score(command, needle) }))
      .filter((entry) => entry.rank !== Number.POSITIVE_INFINITY)
      .sort((a, b) => a.rank - b.rank)
      .map((entry) => entry.command);
  }, [commands, query]);

  // Keep the highlight inside the list as it shrinks under typing.
  useEffect(() => { setCursor(0); }, [query]);

  function onKeyDown(event: React.KeyboardEvent) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setCursor((c) => Math.min(c + 1, matches.length - 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setCursor((c) => Math.max(c - 1, 0));
    } else if (event.key === "Enter") {
      event.preventDefault();
      const chosen = matches[cursor];
      if (chosen) { onRun(chosen.id); onClose(); }
    } else if (event.key === "Escape") {
      event.preventDefault();
      onClose();
    }
  }

  return (
    <div className="modal-backdrop palette-backdrop" onClick={onClose}>
      <div className="palette" onClick={(e) => e.stopPropagation()}
           role="dialog" aria-label="Jump to a tool">
        <input
          ref={inputRef}
          className="palette-input"
          placeholder="Jump to a tool…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={onKeyDown}
          aria-label="Search tools"
        />

        <div className="palette-list">
          {matches.length === 0 && (
            <div className="palette-empty small muted">
              Nothing matches “{query}”.
            </div>
          )}
          {matches.map((command, index) => (
            <button
              key={command.id}
              className={`palette-item ${index === cursor ? "active" : ""}`}
              onMouseEnter={() => setCursor(index)}
              onClick={() => { onRun(command.id); onClose(); }}
            >
              <span className="palette-label">{command.label}</span>
              <span className="palette-group small muted">{command.group}</span>
              {command.hint && (
                <span className="palette-hint small muted">{command.hint}</span>
              )}
            </button>
          ))}
        </div>

        <div className="palette-foot small muted">
          ↑↓ to move · ↵ to open · esc to close
        </div>
      </div>
    </div>
  );
}
