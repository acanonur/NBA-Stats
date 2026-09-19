import { useEffect, type JSX } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthProvider";
import { Text } from "../design/Text";

export default function Landing(): JSX.Element {
  const { user, isLoading } = useAuth();
  const navigate = useNavigate();

  useEffect(() => {
    if (isLoading || !user) return;
    void navigate(user.selectedDashboardId ? `/d/${user.selectedDashboardId}` : "/presets", {
      replace: true,
    });
  }, [isLoading, user, navigate]);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--hw-space-md)", maxWidth: 560 }}>
      <Text as="p" style="displayValue">
        Hardwood
      </Text>
      <Text as="p" style="tableCell" color="secondary">
        Daily stats, last night's results, the player you follow, and fantasy numbers that come
        straight from the box score, back to 1946.
      </Text>
    </div>
  );
}
