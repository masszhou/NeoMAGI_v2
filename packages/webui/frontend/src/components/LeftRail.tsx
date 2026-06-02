import {
  IconChatCircleDots,
  IconCheckSquare,
  IconCpu,
  IconGear,
  IconUsers,
} from "../icons";

export type Section = "chat" | "tasks" | "people" | "system";

const ITEMS = [
  { id: "chat", label: "Chat", icon: IconChatCircleDots, live: true },
  { id: "tasks", label: "Projects", icon: IconCheckSquare, live: true },
  { id: "people", label: "Members", icon: IconUsers, live: true },
  { id: "system", label: "System", icon: IconCpu, live: false },
] as const;

export function LeftRail({
  section,
  onPick,
}: {
  section: Section;
  onPick: (section: Section) => void;
}) {
  return (
    <nav className="rail">
      <div className="rail__mono" title="NeoMAGI">
        N
      </div>
      <div className="rail__icons">
        {ITEMS.map((it) => {
          const Glyph = it.icon;
          return (
            <button
              key={it.id}
              className="rail__icon"
              data-active={section === it.id ? "true" : "false"}
              data-disabled={!it.live}
              onClick={() => it.live && onPick(it.id)}
              title={it.live ? it.label : it.label + " (coming soon)"}
            >
              <Glyph size={20} />
            </button>
          );
        })}
      </div>
      <div className="rail__bottom">
        <button
          className="rail__icon"
          data-disabled="true"
          title="Settings (coming soon)"
        >
          <IconGear size={20} />
        </button>
      </div>
    </nav>
  );
}
