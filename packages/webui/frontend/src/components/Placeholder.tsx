import type { ReactNode } from "react";

export function Placeholder({
  seal,
  title,
  sub,
}: {
  seal?: ReactNode;
  title: string;
  sub: string;
}) {
  return (
    <div className="placeholder">
      <div className="placeholder__inner">
        <div className="placeholder__seal">{seal ?? "◷"}</div>
        <div className="placeholder__title">{title}</div>
        <div className="placeholder__sub">{sub}</div>
      </div>
    </div>
  );
}
