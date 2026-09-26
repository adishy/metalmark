// Insights: Reports' old destination, renamed and given room to grow.
// `/reports` used to be one page; this is a shell around several, tabbed with
// `ScrollTabs` so a phone reader swipes between them the way Settings' eleven
// sections already work (§5, and see ScrollTabs.tsx for why the same strip is
// reused rather than reinvented here).
//
// Tab state lives in the URL (`/insights/:tab`), not in useState like
// Settings' — a report a reader is looking at is exactly the kind of state a
// deep link or Back button should reach, the same reasoning RangeControl
// already applies to the window within a report.
import { Navigate, useNavigate, useParams } from "react-router-dom";
import ScrollTabs from "@/components/ScrollTabs";
import Overview from "@/pages/insights/Overview";
import Allocations from "@/pages/insights/Allocations";
import Recurring from "@/pages/insights/Recurring";

// One declared list drives the tab strip, the route guard and which panel
// renders — adding a tab is one line here plus one line in the switch below;
// nothing else needs to know the list grew.
const TABS = [
  { id: "overview", label: "Overview" },
  { id: "allocations", label: "Allocations" },
  { id: "recurring", label: "Recurring" },
] as const;

type TabId = (typeof TABS)[number]["id"];

const DEFAULT_TAB: TabId = "overview";

function isTabId(v: string | undefined): v is TabId {
  return TABS.some((t) => t.id === v);
}

export default function Insights() {
  const { tab } = useParams<{ tab: string }>();
  const navigate = useNavigate();

  // An unknown or missing tab (a stale link, a typo) lands on the default
  // rather than a blank panel — the same "there is always a usable page"
  // rule `/reports`'s own redirect below follows.
  if (!isTabId(tab)) return <Navigate to={`/insights/${DEFAULT_TAB}`} replace />;

  return (
    // `max-w-6xl`: §9.1's two-up width, which Overview's charts need and
    // Allocations' bars don't stretch past either. Owned here rather than by
    // each tab, so every tab shares one width instead of jumping as you
    // switch between them.
    <div className="mx-auto max-w-6xl space-y-4">
      <h1 className="text-xl font-semibold">Insights</h1>
      <ScrollTabs
        tabs={TABS}
        selected={tab}
        onSelect={(id) => navigate(`/insights/${id}`)}
        ariaLabel="Insights sections"
        testidPrefix="insights-tab"
      />
      <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`} data-testid={`insights-panel-${tab}`}>
        {tab === "overview" && <Overview />}
        {tab === "allocations" && <Allocations />}
        {tab === "recurring" && <Recurring />}
      </div>
    </div>
  );
}
