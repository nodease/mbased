import Sidebar from '../features/dashboard/components/Sidebar';
import ActiveOrganizationGate from '../features/dashboard/components/ActiveOrganizationGate';

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <ActiveOrganizationGate>
      <div className="flex h-screen overflow-hidden bg-white font-sans text-slate-950">
        <Sidebar />

        <div className="relative z-0 flex flex-1 flex-col overflow-hidden bg-white">
          <main className="flex-1 overflow-y-auto">{children}</main>
        </div>
      </div>
    </ActiveOrganizationGate>
  );
}
