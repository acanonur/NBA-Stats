/** `/legal/terms` — required by the account system (WEB_DESIGN.md §11). Summarises
 * `docs/LEGAL.md`; not legal advice, and not a substitute for reading that document if this
 * deployment is ever made public. */
import type { JSX } from "react";
import { Text } from "../../design/Text";

export default function Terms(): JSX.Element {
  return (
    <div style={{ maxWidth: 640, display: "flex", flexDirection: "column", gap: "var(--hw-space-md)" }}>
      <Text as="p" style="sectionTitle">
        Terms of use
      </Text>
      <Text as="p" style="tableCell">
        Hardwood is a private stats and fantasy-analysis tool. Statistics shown here come from
        stats.nba.com and are used for private, non-commercial purposes only, with attribution in
        every page's footer. Hardwood does not run a fantasy league, keep scores, price anything,
        or depict a game in real time.
      </Text>
      <Text as="p" style="tableCell">
        By creating an account you agree that you will not use Hardwood to run a public,
        commercial, gambling, or fantasy-contest service, and that you understand the numbers
        here are analysis of publicly reported statistics, not licensed data.
      </Text>
      <Text as="p" style="tableCell">
        This page is a summary, not legal advice. See this project's <code>docs/LEGAL.md</code>{" "}
        for the full accounting of what governs the underlying data.
      </Text>
    </div>
  );
}
