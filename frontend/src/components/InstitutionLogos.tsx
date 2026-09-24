// Which institutions have a logo, for every account mark on the page.
//
// A context rather than a query inside `AccountMark`: the mark is drawn in
// dozens of places, several of them in tests with no query client, and a mark
// that needed one would make every one of those a network test. Outside the
// provider the map is empty and a mark draws its initials, exactly as before.
import { createContext, useContext, useMemo, type ReactNode } from "react";
import { institutionKey, logoUrl, useInstitutions } from "@/api/institutions";

const LogoContext = createContext<ReadonlyMap<string, string>>(new Map());

export function InstitutionLogosProvider({ children }: { children: ReactNode }) {
  const institutions = useInstitutions();
  const map = useMemo(() => {
    const m = new Map<string, string>();
    institutions.data?.forEach((i) => {
      if (i.has_logo) m.set(i.key, logoUrl(i));
    });
    return m;
  }, [institutions.data]);
  return <LogoContext.Provider value={map}>{children}</LogoContext.Provider>;
}

/** The logo URL for an institution, if the household has one. */
export function useInstitutionLogo(institution: string | null | undefined): string | undefined {
  const map = useContext(LogoContext);
  return institution ? map.get(institutionKey(institution)) : undefined;
}
