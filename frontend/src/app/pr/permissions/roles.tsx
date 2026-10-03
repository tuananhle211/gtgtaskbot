"use client";

import { useQuery } from "@tanstack/react-query";
import type { CapabilityDescriptor, RoleSummary } from "@/lib/api";
import { api } from "@/lib/api";
import { Empty, ErrorBox, Loading, Pill } from "@/components/states";

/**
 * *Vai trò & quyền.* The four base roles and what the permission matrix gives
 * each. **Read-only.** Roles are enumerated in code (`Role`, and the matrix in
 * `domain/permissions/matrix.py`); there is no role table, no custom role and
 * nothing to edit - phase 1 shows the model as it is rather than pretending a
 * configurable one exists.
 *
 * Every word comes from the server: the role's name, the capability's name,
 * the domain it belongs to, and the sentence about grants at the bottom.
 */
export function RolesTab() {
  const roles = useQuery({ queryKey: ["roles"], queryFn: api.roles });

  if (roles.isPending) return <Loading label="Đang tải vai trò…" />;
  if (roles.isError) return <ErrorBox error={roles.error} onRetry={() => roles.refetch()} />;
  if (roles.data.roles.length === 0) return <Empty message="Chưa có vai trò nào." />;

  return (
    <div className="space-y-4">
      <p className="text-sm text-[var(--text-muted)]">
        Mỗi thành viên có đúng một vai trò nền. Vai trò quyết định các quyền mặc định dưới đây;
        không tạo được vai trò mới và không sửa được quyền của vai trò ở đây.
      </p>
      <div className="grid gap-4 lg:grid-cols-2">
        {roles.data.roles.map((role) => (
          <RoleCard key={role.role} role={role} />
        ))}
      </div>
      <p className="rounded bg-[var(--surface-muted)] p-3 text-xs text-[var(--text-muted)]">
        <strong>{roles.data.note}</strong> Quyền duyệt được cấp ở thẻ “Quyền duyệt cấp thêm”.
      </p>
    </div>
  );
}

function RoleCard({ role }: { role: RoleSummary }) {
  const groups = new Map<string, CapabilityDescriptor[]>();
  for (const capability of role.capabilities) {
    const list = groups.get(capability.domain_label) ?? [];
    list.push(capability);
    groups.set(capability.domain_label, list);
  }
  return (
    <section
      data-testid="role-card"
      className="rounded-lg border border-[var(--border)] bg-[var(--surface)] p-4"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold">{role.label}</h2>
        <div className="flex items-center gap-2">
          <Pill tone="neutral">{role.active_member_count} thành viên đang hoạt động</Pill>
          {role.assignable ? null : <Pill tone="warn">Không gán được</Pill>}
        </div>
      </div>
      {role.assignable ? null : (
        <p className="mt-1 text-xs text-[var(--text-muted)]">
          Chủ sở hữu là quyền sở hữu không gian làm việc, được cấu hình khi triển khai — không phải
          vai trò để gán cho thành viên.
        </p>
      )}
      {groups.size === 0 ? (
        <p className="mt-3 text-sm text-[var(--text-muted)]">
          Vai trò này không có quyền mặc định.
        </p>
      ) : (
        <dl className="mt-3 space-y-2 text-sm">
          {Array.from(groups.entries()).map(([domain, capabilities]) => (
            <div key={domain}>
              <dt className="text-xs uppercase tracking-wide text-[var(--text-muted)]">{domain}</dt>
              <dd className="mt-1 flex flex-wrap gap-1.5">
                {capabilities.map((capability) => (
                  <Pill key={capability.capability} tone="good">
                    {capability.label}
                  </Pill>
                ))}
              </dd>
            </div>
          ))}
        </dl>
      )}
    </section>
  );
}
