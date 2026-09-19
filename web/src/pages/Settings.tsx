/**
 * `/settings` — account, identities, sessions, favourites, theme, export, delete
 * (WEB_DESIGN.md §7.2).
 */
import { useState, type JSX } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../auth/AuthProvider";
import { useProviders } from "../auth/useProviders";
import {
  ApiError,
  changePassword,
  deleteAccount,
  exportAccount,
  listSessions,
  revokeSession,
  startIdentityLink,
  unlinkIdentity,
} from "../api/session";
import { Text } from "../design/Text";
import formStyles from "../auth/forms.module.css";
import styles from "./Settings.module.css";

function AccountSection(): JSX.Element {
  const { user, updateProfile } = useAuth();
  const [displayName, setDisplayName] = useState(user?.displayName ?? "");
  const [status, setStatus] = useState<string | null>(null);

  return (
    <div className={styles.section}>
      <Text as="p" style="sectionTitle">
        Account
      </Text>
      <Text style="caption" color="secondary">
        {user?.email ?? "No email on file"} {user?.emailVerified ? "· verified" : "· not verified"}
      </Text>
      <label className={formStyles.field}>
        <Text style="tableHeader" as="span">
          Display name
        </Text>
        <input
          className={formStyles.input}
          type="text"
          value={displayName}
          onChange={(event) => setDisplayName(event.target.value)}
        />
      </label>
      <button
        type="button"
        className={styles.button}
        onClick={() => {
          void updateProfile({ displayName }).then(() => setStatus("Saved."));
        }}
      >
        Save name
      </button>
      {status && (
        <Text style="caption" color="positive">
          {status}
        </Text>
      )}
    </div>
  );
}

function ThemeSection(): JSX.Element {
  const { user, updateProfile } = useAuth();
  return (
    <div className={styles.section}>
      <Text as="p" style="sectionTitle">
        Theme
      </Text>
      <select
        value={user?.theme ?? "system"}
        onChange={(event) => void updateProfile({ theme: event.target.value as "system" | "light" | "dark" })}
      >
        <option value="system">Match system</option>
        <option value="light">Light</option>
        <option value="dark">Dark</option>
      </select>
    </div>
  );
}

function FavoritesSection(): JSX.Element {
  const { user, updateProfile } = useAuth();
  return (
    <div className={styles.section}>
      <Text as="p" style="sectionTitle">
        Favourites
      </Text>
      <div className={styles.row}>
        <Text style="tableCell">Favourite player id: {user?.favoritePlayerId ?? "none"}</Text>
        {user?.favoritePlayerId !== null && user?.favoritePlayerId !== undefined && (
          <button type="button" className={styles.button} onClick={() => void updateProfile({ favoritePlayerId: null })}>
            Clear
          </button>
        )}
      </div>
      <div className={styles.row}>
        <Text style="tableCell">Favourite team id: {user?.favoriteTeamId ?? "none"}</Text>
        {user?.favoriteTeamId !== null && user?.favoriteTeamId !== undefined && (
          <button type="button" className={styles.button} onClick={() => void updateProfile({ favoriteTeamId: null })}>
            Clear
          </button>
        )}
      </div>
    </div>
  );
}

function PasswordSection(): JSX.Element {
  const { user } = useAuth();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [message, setMessage] = useState<string | null>(null);

  return (
    <div className={styles.section}>
      <Text as="p" style="sectionTitle">
        Password
      </Text>
      {user?.hasPassword && (
        <label className={formStyles.field}>
          <Text style="tableHeader" as="span">
            Current password
          </Text>
          <input
            className={formStyles.input}
            type="password"
            value={current}
            onChange={(event) => setCurrent(event.target.value)}
          />
        </label>
      )}
      <label className={formStyles.field}>
        <Text style="tableHeader" as="span">
          {user?.hasPassword ? "New password" : "Set a password"}
        </Text>
        <input className={formStyles.input} type="password" value={next} onChange={(event) => setNext(event.target.value)} />
      </label>
      <button
        type="button"
        className={styles.button}
        onClick={() => {
          setMessage(null);
          changePassword({ currentPassword: current || null, newPassword: next })
            .then(() => setMessage("Password updated. Other sessions were signed out."))
            .catch((cause: unknown) => setMessage(cause instanceof ApiError ? cause.message : "Could not update the password."));
        }}
      >
        Save password
      </button>
      {message && <Text style="caption">{message}</Text>}
    </div>
  );
}

function IdentitiesSection(): JSX.Element {
  const { user, refresh } = useAuth();
  const { isGoogleEnabled, isAppleEnabled } = useProviders();
  const identities = new Set(user?.identities ?? []);

  async function link(provider: "google" | "apple"): Promise<void> {
    const result = await startIdentityLink(provider);
    // A top-level navigation to the OAuth authorize URL the server just minted, not component
    // state — `window.location` is the browser's own global, not a value this component owns.
    // eslint-disable-next-line react-hooks/immutability
    window.location.href = result.redirectUrl;
  }

  async function unlink(provider: "google" | "apple"): Promise<void> {
    await unlinkIdentity(provider);
    await refresh();
  }

  return (
    <div className={styles.section}>
      <Text as="p" style="sectionTitle">
        Sign-in methods
      </Text>
      {(["google", "apple"] as const).map((provider) => {
        const enabled = provider === "google" ? isGoogleEnabled : isAppleEnabled;
        if (!enabled && !identities.has(provider)) return null;
        return (
          <div key={provider} className={styles.row}>
            <Text style="tableCell">{provider === "google" ? "Google" : "Apple"}</Text>
            {identities.has(provider) ? (
              <button type="button" className={styles.button} onClick={() => void unlink(provider)}>
                Unlink
              </button>
            ) : (
              <button type="button" className={styles.button} onClick={() => void link(provider)}>
                Link
              </button>
            )}
          </div>
        );
      })}
    </div>
  );
}

function SessionsSection(): JSX.Element {
  const { data, refetch } = useQuery({ queryKey: ["me", "sessions"], queryFn: listSessions });
  return (
    <div className={styles.section}>
      <Text as="p" style="sectionTitle">
        Sessions
      </Text>
      {(data ?? []).map((session) => (
        <div key={session.sessionId} className={styles.row}>
          <div>
            <Text style="tableCell">{session.userAgent ?? "Unknown device"}{session.current ? " (this device)" : ""}</Text>
            <Text style="caption" color="secondary">
              Last seen {session.lastSeenAt}
            </Text>
          </div>
          {!session.current && (
            <button
              type="button"
              className={styles.button}
              onClick={() => {
                void revokeSession(session.sessionId).then(() => refetch());
              }}
            >
              Sign out
            </button>
          )}
        </div>
      ))}
    </div>
  );
}

function ExportSection(): JSX.Element {
  const [isExporting, setIsExporting] = useState(false);

  async function download(): Promise<void> {
    setIsExporting(true);
    try {
      const data = await exportAccount();
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "hardwood-account-export.json";
      link.click();
      URL.revokeObjectURL(url);
    } finally {
      setIsExporting(false);
    }
  }

  return (
    <div className={styles.section}>
      <Text as="p" style="sectionTitle">
        Export your data
      </Text>
      <button type="button" className={styles.button} disabled={isExporting} onClick={() => void download()}>
        {isExporting ? "Preparing…" : "Download my data"}
      </button>
    </div>
  );
}

function DeleteAccountSection(): JSX.Element {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function confirmDelete(): Promise<void> {
    try {
      await deleteAccount();
      queryClient.clear();
      await navigate("/", { replace: true });
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "Could not delete this account.");
    }
  }

  return (
    <div className={styles.section}>
      <Text as="p" style="sectionTitle">
        Delete account
      </Text>
      <Text as="p" style="caption" color="secondary">
        Your account is disabled immediately and erased permanently after 30 days.
      </Text>
      {!confirming ? (
        <button type="button" className={`${styles.button} ${styles.dangerButton}`} onClick={() => setConfirming(true)}>
          Delete my account
        </button>
      ) : (
        <div>
          <Text as="p" style="tableCell">
            Are you sure? This cannot be undone after 30 days.
          </Text>
          <button type="button" className={`${styles.button} ${styles.dangerButton}`} onClick={() => void confirmDelete()}>
            Yes, delete my account
          </button>
          <button type="button" className={styles.button} onClick={() => setConfirming(false)}>
            Cancel
          </button>
        </div>
      )}
      {error && <Text style="caption" className={formStyles.errorText}>{error}</Text>}
    </div>
  );
}

export default function Settings(): JSX.Element {
  return (
    <div>
      <Text as="p" style="displayValue">
        Settings
      </Text>
      <AccountSection />
      <ThemeSection />
      <FavoritesSection />
      <PasswordSection />
      <IdentitiesSection />
      <SessionsSection />
      <ExportSection />
      <DeleteAccountSection />
    </div>
  );
}
