import { Nav } from "@/components/Nav";
import { ServerStatusBanner } from "@/components/ServerStatusBanner";
import { AppRoutes } from "@/routes";

export default function App() {
  return (
    <div className="flex h-screen flex-col bg-background text-foreground">
      <ServerStatusBanner />
      <div className="flex min-h-0 flex-1">
        <Nav />
        <main className="min-h-0 flex-1 overflow-y-auto px-10 py-8">
          <AppRoutes />
        </main>
      </div>
    </div>
  );
}
