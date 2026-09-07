import { useRef } from "react";

export function DetailTabs<T extends string>({ id, label, tabs, selected, onSelect }: {
  id: string;
  label: string;
  tabs: readonly { id: T; label: string }[];
  selected: T;
  onSelect: (value: T) => void;
}) {
  const buttons = useRef<(HTMLButtonElement | null)[]>([]);
  return <div className="league-detail-tabs" role="tablist" aria-label={label}>
    {tabs.map((tab, index) => <button key={tab.id} ref={(element) => { buttons.current[index] = element; }}
      type="button" role="tab" id={`${id}-tab-${tab.id}`} aria-controls={`${id}-panel-${tab.id}`}
      aria-selected={selected === tab.id} tabIndex={selected === tab.id ? 0 : -1}
      onClick={() => onSelect(tab.id)} onKeyDown={(event) => {
        const next = event.key === "ArrowRight" ? (index + 1) % tabs.length
          : event.key === "ArrowLeft" ? (index + tabs.length - 1) % tabs.length
          : event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : null;
        if (next === null) return;
        event.preventDefault();
        onSelect(tabs[next].id);
        buttons.current[next]?.focus();
      }}>{tab.label}</button>)}
  </div>;
}
