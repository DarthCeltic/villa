import { useState, type ReactNode } from "react";

export function Disclosure({
  summary,
  children,
  className = "",
  summaryClassName = "",
  defaultOpen = false,
  ...rest
}: {
  summary: ReactNode;
  children: ReactNode;
  className?: string;
  summaryClassName?: string;
  defaultOpen?: boolean;
} & Record<`data-${string}`, string | undefined>) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <details
      className={className}
      open={open}
      onToggle={(e) => setOpen((e.currentTarget as HTMLDetailsElement).open)}
      {...rest}
    >
      <summary className={summaryClassName}>{summary}</summary>
      {open ? children : null}
    </details>
  );
}
