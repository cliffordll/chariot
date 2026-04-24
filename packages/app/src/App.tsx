import { Nav } from "@/components/Nav";
import { ServerStatusBanner } from "@/components/ServerStatusBanner";
import { AppRoutes } from "@/routes";

export default function App() {
  return (
    <div className="flex min-h-screen flex-col bg-background text-foreground">
      <ServerStatusBanner />
      <div className="flex flex-1">
        <Nav />
        <main className="flex-1 px-10 py-8">
          <AppRoutes />
        </main>
      </div>
    </div>
  );
}
