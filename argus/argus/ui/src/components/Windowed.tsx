import { useState } from "react";

export function Windowed<T>({
  items,
  rowKey,
  render,
  page = 12,
  label = "item",
}: {
  items: T[];
  rowKey: (t: T, i: number) => string;
  render: (t: T, i: number) => React.ReactNode;
  page?: number;
  label?: string;
}) {
  const [shown, setShown] = useState(page);
  const visible = items.slice(0, shown);
  const remaining = items.length - visible.length;
  return (
    <div>
      <div style={{ display: "grid", gap: 4 }}>
        {visible.map((t, i) => (
          <div key={rowKey(t, i)}>{render(t, i)}</div>
        ))}
      </div>
      {remaining > 0 ? (
        <button
          type="button"
          className="btn"
          style={{ marginTop: 8 }}
          onClick={() => setShown(shown + page)}
        >
          Show {Math.min(page, remaining)} more of {remaining} remaining {label}
          {remaining === 1 ? "" : "s"}
        </button>
      ) : null}
    </div>
  );
}
