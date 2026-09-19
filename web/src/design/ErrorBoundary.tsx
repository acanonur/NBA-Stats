/**
 * The two error boundaries this app was shipped without.
 *
 * React only stops a render-time throw at a **class** component that implements
 * `getDerivedStateFromError`. There was none anywhere in `web/src`, and `routes.tsx` declared
 * no `errorElement`, so any widget that threw while rendering unmounted `RootLayout` itself:
 * the reader lost the header, the nav, every other tile on the dashboard and the NBA
 * attribution footer that has to be on every page, and got React Router's default
 * "Unexpected Application Error!" with a stack trace.
 *
 * Five of the sixteen widgets decoded their payload bare in render (`scoreboard`, `stat_tile`,
 * `comparison`, `daily_movers`, `shot_profile`), and those decoders throw on any shape
 * deviation — a score that arrives as the string "112", a `topPerformers` that came back null
 * for a game with no box score yet. That is now caught twice: `WidgetContainer` decodes inside
 * a try/catch it owns, so no future widget can forget it, and `TileErrorBoundary` below stops
 * anything the try/catch does not.
 */
import { Component, type ErrorInfo, type JSX, type ReactNode } from "react";
import type { WidgetSizeKey } from "../generated/tokens";
import { ErrorTile } from "./StateViews";

interface Props {
  readonly children: ReactNode;
  /** Rendered instead of the children once a descendant has thrown. */
  readonly fallback: (error: Error, reset: () => void) => ReactNode;
  /** Changing this resets the boundary — used to clear the error when a retry lands. */
  readonly resetKey?: unknown;
}

interface State {
  readonly error: Error | null;
  readonly resetKey: unknown;
}

export class ErrorBoundary extends Component<Props, State> {
  public constructor(props: Props) {
    super(props);
    this.state = { error: null, resetKey: props.resetKey };
  }

  public static getDerivedStateFromError(error: Error): Partial<State> {
    return { error };
  }

  public static getDerivedStateFromProps(props: Props, state: State): Partial<State> | null {
    if (props.resetKey !== state.resetKey) return { error: null, resetKey: props.resetKey };
    return null;
  }

  public componentDidCatch(error: Error, info: ErrorInfo): void {
    // The console is the only place this can go: there is no error tracker in this product and
    // adding one would be a third-party request from a page that serves session cookies.
    console.error("Hardwood caught a render error", error, info.componentStack);
  }

  private readonly reset = (): void => this.setState({ error: null });

  public render(): ReactNode {
    const { error } = this.state;
    if (error) return this.props.fallback(error, this.reset);
    return this.props.children;
  }
}

export interface TileErrorBoundaryProps {
  readonly children?: ReactNode;
  readonly size: WidgetSizeKey;
  readonly resetKey?: unknown;
  readonly onRetry?: () => void;
}

/** One tile's blast radius. A widget that throws degrades to the same grey `ErrorTile` the
 * eleven widgets with their own try/catch already show, instead of taking the route down. */
export function TileErrorBoundary({
  children,
  size,
  resetKey,
  onRetry,
}: TileErrorBoundaryProps): JSX.Element {
  return (
    <ErrorBoundary
      resetKey={resetKey}
      fallback={(_error, reset) => (
        <ErrorTile
          size={size}
          message="This widget's data did not match what the app expected."
          isRetryable={onRetry !== undefined}
          onRetry={() => {
            reset();
            onRetry?.();
          }}
        />
      )}
    >
      {children}
    </ErrorBoundary>
  );
}
