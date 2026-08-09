import { Component, type ReactNode } from "react";

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
}

interface State {
  hasError: boolean;
  message: string;
}

export default class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, message: "" };
  }

  static getDerivedStateFromError(error: unknown): State {
    const message =
      error instanceof Error ? error.message : "An unexpected error occurred.";
    return { hasError: true, message };
  }

  render() {
    if (this.state.hasError) {
      return (
        this.props.fallback ?? (
          <div className="max-w-xl mx-auto px-4 py-12 text-center">
            <p className="text-sm font-medium text-red-600">Something went wrong</p>
            <p className="text-xs text-gray-400 mt-1">{this.state.message}</p>
            <button
              className="mt-4 text-xs text-blue-600 hover:underline"
              onClick={() => this.setState({ hasError: false, message: "" })}
            >
              Try again
            </button>
          </div>
        )
      );
    }
    return this.props.children;
  }
}
