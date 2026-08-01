import {
  useCallback,
  useDeferredValue,
  useEffect,
  useMemo,
  useState,
  type FormEvent,
} from "react";
import { Link, useSearchParams } from "react-router-dom";
import { StatusBadge } from "../components/StatusBadge";
import {
  createManagedTenant,
  createManagedUnit,
  createManagedUser,
  deleteManagedTenant,
  deleteManagedUnit,
  deleteManagedUser,
  getManagedTenants,
  getManagedUsers,
  updateManagedTenant,
  updateManagedUnit,
  updateManagedUser,
  type CurrentUser,
  type ManagedTenant,
  type ManagedUser,
} from "../lib/api";

type ManagedRole = CurrentUser["role"];
type ManagedUnit = ManagedTenant["units"][number];
type AccessDirectory = "tenants" | "users";
type AccessDrawer =
  | { kind: "create-tenant" }
  | { kind: "create-user" }
  | { kind: "edit-tenant"; tenant: ManagedTenant }
  | { kind: "add-village"; tenant: ManagedTenant }
  | { kind: "edit-unit"; unit: ManagedUnit }
  | { kind: "edit-user"; user: ManagedUser }
  | { kind: "reset-password"; user: ManagedUser }
  | null;
type SuspendTarget =
  | { kind: "tenant"; tenant: ManagedTenant }
  | { kind: "user"; user: ManagedUser }
  | { kind: "unit"; unit: ManagedUnit }
  | null;

const roleLabels: Record<ManagedRole, string> = {
  platform_admin: "平台管理员",
  tenant_admin: "租户管理员",
  village_operator: "村级数据员",
};

const DIRECTORY_PAGE_SIZE = 8;

export function AccessManagementPage({
  currentUser,
}: {
  currentUser: CurrentUser;
}) {
  const [searchParams, setSearchParams] = useSearchParams();
  const directory: AccessDirectory =
    searchParams.get("type") === "users" ? "users" : "tenants";
  const query = searchParams.get("q") ?? "";
  const statusFilter = searchParams.get("status") ?? "all";
  const roleFilter = searchParams.get("role") ?? "all";
  const requestedPage = Math.max(1, Number(searchParams.get("page") ?? "1") || 1);
  const selectedId = searchParams.get("selected") ?? "";
  const deferredQuery = useDeferredValue(query);

  const [tenants, setTenants] = useState<ManagedTenant[]>([]);
  const [tenantOptions, setTenantOptions] = useState<ManagedTenant[]>([]);
  const [users, setUsers] = useState<ManagedUser[]>([]);
  const [directoryTotal, setDirectoryTotal] = useState(0);
  const [createTenantId, setCreateTenantId] = useState("");
  const [createRole, setCreateRole] = useState<ManagedRole>("tenant_admin");
  const [createScopeId, setCreateScopeId] = useState("");
  const [editTenantId, setEditTenantId] = useState("");
  const [editRole, setEditRole] = useState<ManagedRole>("tenant_admin");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [drawer, setDrawer] = useState<AccessDrawer>(null);
  const [suspendTarget, setSuspendTarget] = useState<SuspendTarget>(null);
  const [inspectorOpen, setInspectorOpen] = useState(false);

  const updateQuery = useCallback((
    patch: Record<string, string | null>,
    options: { replace?: boolean; resetPage?: boolean } = {},
  ) => {
    const next = new URLSearchParams(searchParams);
    for (const [key, value] of Object.entries(patch)) {
      if (!value || value === "all") next.delete(key);
      else next.set(key, value);
    }
    if (options.resetPage) {
      next.delete("page");
      next.delete("selected");
    }
    setSearchParams(next, { replace: options.replace });
  }, [searchParams, setSearchParams]);

  const refresh = useCallback(async (signal?: AbortSignal) => {
    try {
      const offset = (requestedPage - 1) * DIRECTORY_PAGE_SIZE;
      const [nextTenants, nextUsers, nextTenantOptions] = await Promise.all([
        getManagedTenants(
          directory === "tenants"
            ? {
                search: deferredQuery.trim(),
                status: statusFilter,
                limit: DIRECTORY_PAGE_SIZE,
                offset,
              }
            : { limit: 100 },
          signal,
        ),
        getManagedUsers(
          directory === "users"
            ? {
                search: deferredQuery.trim(),
                status: statusFilter,
                role: roleFilter,
                limit: DIRECTORY_PAGE_SIZE,
                offset,
              }
            : { limit: 100 },
          signal,
        ),
        getManagedTenants({ limit: 100 }, signal),
      ]);
      setTenants(nextTenants.items);
      setTenantOptions(nextTenantOptions.items);
      setUsers(nextUsers.items);
      setDirectoryTotal(
        directory === "tenants" ? nextTenants.total : nextUsers.total,
      );
      setCreateTenantId((current) => {
        if (nextTenantOptions.items.some((tenant) => tenant.id === current && tenant.status === "active")) {
          return current;
        }
        return nextTenantOptions.items.find((tenant) => tenant.kind === "business" && tenant.status === "active")?.id
          ?? nextTenantOptions.items.find((tenant) => tenant.status === "active")?.id
          ?? "";
      });
      setError("");
    } catch (cause) {
      if (cause instanceof DOMException && cause.name === "AbortError") return;
      setError(cause instanceof Error ? cause.message : "用户与租户加载失败");
    }
  }, [deferredQuery, directory, requestedPage, roleFilter, statusFilter]);

  useEffect(() => {
    const controller = new AbortController();
    void refresh(controller.signal);
    return () => controller.abort();
  }, [refresh]);

  useEffect(() => {
    if (!drawer && !suspendTarget) return;
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      setDrawer(null);
      setSuspendTarget(null);
    }
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [drawer, suspendTarget]);

  const tenantStats = useMemo(() => {
    const next = new Map<string, { users: number; activeUsers: number; villages: number }>();
    for (const tenant of tenants) {
      next.set(tenant.id, {
        users: 0,
        activeUsers: 0,
        villages: tenant.units.filter((unit) => unit.unit_type === "village").length,
      });
    }
    for (const user of users) {
      const stats = next.get(user.tenant_id);
      if (!stats) continue;
      stats.users += 1;
      if (user.user_status === "active") stats.activeUsers += 1;
    }
    return next;
  }, [tenants, users]);

  const filteredTenants = tenants;
  const filteredUsers = users;
  const activeCount = directoryTotal;
  const pageCount = Math.max(1, Math.ceil(directoryTotal / DIRECTORY_PAGE_SIZE));
  const page = Math.min(requestedPage, pageCount);
  const pageTenants = filteredTenants;
  const pageUsers = filteredUsers;
  const selectedTenant =
    directory === "tenants"
      ? filteredTenants.find((tenant) => tenant.id === selectedId) ?? pageTenants[0] ?? null
      : null;
  const selectedUser =
    directory === "users"
      ? filteredUsers.find((user) => user.membership_id === selectedId) ?? pageUsers[0] ?? null
      : null;
  const createTenant = tenantOptions.find((tenant) => tenant.id === createTenantId) ?? null;
  const createScopeUnits =
    createTenant?.units.filter((unit) => {
      if (unit.status !== "active") return false;
      return createRole === "tenant_admin"
        ? unit.unit_type === "township"
        : unit.unit_type === "village";
    }) ?? [];

  async function run(action: () => Promise<unknown>, success: string) {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await action();
      await refresh();
      setMessage(success);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "操作失败");
    } finally {
      setBusy(false);
    }
  }

  async function submitTenant(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    await run(async () => {
      await createManagedTenant(
        String(data.get("name") ?? "").trim(),
        String(data.get("township_name") ?? "").trim(),
      );
      form.reset();
      setDrawer(null);
    }, "业务租户已创建。");
  }

  async function submitUser(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    await run(async () => {
      await createManagedUser({
        username: String(data.get("username") ?? "").trim(),
        display_name: String(data.get("display_name") ?? "").trim(),
        password: String(data.get("password") ?? ""),
        tenant_id: createTenantId,
        role: createRole,
        scope_unit_id:
          createRole === "platform_admin"
            ? null
            : String(data.get("scope_unit_id") ?? "") || null,
      });
      form.reset();
      setCreateScopeId("");
      setDrawer(null);
    }, "用户已创建。");
  }

  async function addCreateScope(name: string): Promise<string> {
    if (!createTenant) throw new Error("请先选择所属租户。");
    const root = createTenant.units.find(
      (unit) => unit.unit_type === "township" && unit.parent_id === null,
    );
    if (!root) throw new Error("当前租户缺少根乡镇，不能新增村。");
    const unit = await createManagedUnit(createTenant.id, {
      name,
      unit_type: "village",
      parent_id: root.id,
    });
    await refresh();
    return unit.id;
  }

  function editUser(user: ManagedUser) {
    setEditTenantId(user.tenant_id);
    setEditRole(user.role);
    setDrawer({ kind: "edit-user", user });
  }

  async function submitDrawer(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!drawer || drawer.kind === "create-tenant" || drawer.kind === "create-user") return;
    const data = new FormData(event.currentTarget);
    if (drawer.kind === "edit-tenant") {
      await run(
        () => updateManagedTenant(drawer.tenant.id, {
          name: String(data.get("name") ?? "").trim(),
        }),
        "租户名称已更新。",
      );
    } else if (drawer.kind === "add-village") {
      const root = drawer.tenant.units.find(
        (unit) => unit.unit_type === "township" && unit.parent_id === null,
      );
      if (!root) {
        setError("当前租户缺少根乡镇，不能新增村。");
        return;
      }
      await run(
        () =>
          createManagedUnit(drawer.tenant.id, {
            name: String(data.get("name") ?? "").trim(),
            unit_type: "village",
            parent_id: root.id,
          }),
        "村级行政区划已创建。",
      );
    } else if (drawer.kind === "edit-unit") {
      await run(
        () =>
          updateManagedUnit(drawer.unit.id, {
            name: String(data.get("name") ?? "").trim(),
          }),
        "行政区划名称已更新。",
      );
    } else if (drawer.kind === "edit-user") {
      await run(
        () =>
          updateManagedUser(drawer.user.user_id, {
            username: String(data.get("username") ?? "").trim(),
            display_name: String(data.get("display_name") ?? "").trim(),
            tenant_id: editTenantId,
            role: editRole,
            scope_unit_id:
              editRole === "platform_admin"
                ? null
                : String(data.get("scope_unit_id") ?? "") || null,
          }),
        "用户、角色和所属范围已更新，原登录会话已撤销。",
      );
    } else {
      await run(
        () =>
          updateManagedUser(drawer.user.user_id, {
            password: String(data.get("password") ?? ""),
          }),
        "用户密码已重置，原登录会话已撤销。",
      );
    }
    setDrawer(null);
  }

  async function confirmSuspend() {
    if (!suspendTarget) return;
    if (suspendTarget.kind === "tenant") {
      await run(() => deleteManagedTenant(suspendTarget.tenant.id), "租户已停用。");
    } else if (suspendTarget.kind === "user") {
      await run(() => deleteManagedUser(suspendTarget.user.user_id), "用户已停用。");
    } else {
      await run(() => deleteManagedUnit(suspendTarget.unit.id), `${suspendTarget.unit.name}已停用。`);
    }
    setSuspendTarget(null);
  }

  function selectTenant(tenant: ManagedTenant) {
    updateQuery({ selected: tenant.id });
    setInspectorOpen(true);
  }

  function selectUser(user: ManagedUser) {
    updateQuery({ selected: user.membership_id });
    setInspectorOpen(true);
  }

  return (
    <section className="access-workbench">
      <header className="access-workbench__header">
        <div>
          <span className="eyebrow">IDENTITY CONTROL PLANE</span>
          <h1>用户与租户控制台</h1>
          <p>管理身份、业务租户和行政范围；控制面不承载村级业务数据。</p>
        </div>
        <div className="access-workbench__header-actions">
          <button
            className="access-refresh"
            type="button"
            aria-label="刷新用户与租户"
            onClick={() => void refresh()}
          >
            刷新
          </button>
          <button
            className="primary-button"
            type="button"
            onClick={() => {
              setCreateScopeId("");
              setDrawer(directory === "tenants" ? { kind: "create-tenant" } : { kind: "create-user" });
            }}
          >
            {directory === "tenants" ? "新增业务租户" : "新增用户"}
          </button>
        </div>
      </header>

      {error ? <p className="alert access-workbench__notice" role="alert">{error}</p> : null}
      {message ? <p className="access-success" role="status">{message}</p> : null}

      <div className="access-workbench__body">
        <nav className="access-kind-nav" aria-label="用户与租户目录">
          <h2>管理对象</h2>
          {(["tenants", "users"] as AccessDirectory[]).map((item) => {
            const next = new URLSearchParams(searchParams);
            next.set("type", item);
            next.delete("page");
            next.delete("selected");
            next.delete("status");
            next.delete("role");
            return (
              <Link
                key={item}
                to={{ search: next.toString() }}
                aria-current={directory === item ? "page" : undefined}
              >
                <span>{item === "tenants" ? "租户" : "用户"}</span>
                <strong>{item === "tenants" ? tenants.length : users.length}</strong>
              </Link>
            );
          })}
        </nav>

        <section
          className="access-directory"
          aria-label={directory === "tenants" ? "租户目录" : "用户目录"}
        >
          <header className="access-directory__toolbar">
            <label className="access-directory__search">
              <span>搜索当前目录</span>
              <input
                type="search"
                value={query}
                placeholder={
                  directory === "tenants"
                    ? "租户、乡镇或村"
                    : "姓名、账号、角色或范围"
                }
                onChange={(event) =>
                  updateQuery(
                    { q: event.target.value || null },
                    { replace: true, resetPage: true },
                  )
                }
              />
            </label>
            <label>
              <span>状态</span>
              <select
                value={statusFilter}
                onChange={(event) =>
                  updateQuery({ status: event.target.value }, { resetPage: true })
                }
              >
                <option value="all">全部状态</option>
                <option value="active">启用</option>
                <option value="disabled">已停用</option>
              </select>
            </label>
            {directory === "users" ? (
              <label>
                <span>角色</span>
                <select
                  value={roleFilter}
                  onChange={(event) =>
                    updateQuery({ role: event.target.value }, { resetPage: true })
                  }
                >
                  <option value="all">全部角色</option>
                  <option value="platform_admin">平台管理员</option>
                  <option value="tenant_admin">租户管理员</option>
                  <option value="village_operator">村级数据员</option>
                </select>
              </label>
            ) : null}
          </header>

          <div className="access-directory__summary">
            <div>
              <strong>{directory === "tenants" ? "租户目录" : "用户目录"}</strong>
              <span>{activeCount} 条结果</span>
            </div>
            <span>每页 {DIRECTORY_PAGE_SIZE} 条</span>
          </div>

          <div className="access-table-wrap">
            <table className="access-table">
              <thead>
                <tr>
                  <th scope="col">{directory === "tenants" ? "租户" : "用户"}</th>
                  <th scope="col">{directory === "tenants" ? "类型" : "所属租户"}</th>
                  <th scope="col">{directory === "tenants" ? "规模" : "角色与范围"}</th>
                  <th scope="col">状态</th>
                </tr>
              </thead>
              <tbody>
                {directory === "tenants"
                  ? pageTenants.map((tenant) => {
                      const stats = tenantStats.get(tenant.id);
                      return (
                        <tr
                          key={tenant.id}
                          data-selected={selectedTenant?.id === tenant.id || undefined}
                        >
                          <td>
                            <button
                              className="access-row-button"
                              type="button"
                              aria-current={selectedTenant?.id === tenant.id ? "true" : undefined}
                              onClick={() => selectTenant(tenant)}
                            >
                              <strong>{tenant.name}</strong>
                              <small>{tenant.units.map((unit) => unit.name).join("、") || "无行政区划"}</small>
                            </button>
                          </td>
                          <td>{tenant.kind === "platform" ? "控制面租户" : "业务租户"}</td>
                          <td>{stats?.villages ?? 0} 个村 · {stats?.users ?? 0} 个用户</td>
                          <td><StatusBadge status={tenant.status} /></td>
                        </tr>
                      );
                    })
                  : pageUsers.map((user) => (
                      <tr
                        key={user.membership_id}
                        data-selected={selectedUser?.membership_id === user.membership_id || undefined}
                      >
                        <td>
                          <button
                            className="access-row-button"
                            type="button"
                            aria-current={
                              selectedUser?.membership_id === user.membership_id ? "true" : undefined
                            }
                            onClick={() => selectUser(user)}
                          >
                            <strong>{user.display_name}</strong>
                            <small>@{user.username}</small>
                          </button>
                        </td>
                        <td>{user.tenant_name}</td>
                        <td>{roleLabels[user.role]} · {user.scope_unit_name || "无行政范围"}</td>
                        <td><StatusBadge status={user.user_status} /></td>
                      </tr>
                    ))}
              </tbody>
            </table>
            {(directory === "tenants" ? pageTenants : pageUsers).length === 0 ? (
              <div className="access-directory__empty">
                <strong>没有符合条件的对象</strong>
                <p>调整搜索词或筛选条件后再试。</p>
                <button
                  type="button"
                  onClick={() =>
                    updateQuery({ q: null, status: null, role: null, page: null })
                  }
                >
                  清除筛选
                </button>
              </div>
            ) : null}
          </div>

          <footer className="access-pagination" aria-label="目录分页">
            <span>第 {page} / {pageCount} 页</span>
            <div>
              <button
                type="button"
                disabled={page <= 1}
                onClick={() => updateQuery({ page: String(page - 1), selected: null })}
              >
                上一页
              </button>
              <button
                type="button"
                disabled={page >= pageCount}
                onClick={() => updateQuery({ page: String(page + 1), selected: null })}
              >
                下一页
              </button>
            </div>
          </footer>
        </section>

        <aside
          className="access-inspector"
          aria-label="权限边界详情"
          data-open={inspectorOpen || undefined}
        >
          <button
            className="access-inspector__close"
            type="button"
            aria-label="关闭详情"
            onClick={() => setInspectorOpen(false)}
          >
            ×
          </button>
          {selectedTenant ? (
            <TenantInspector
              currentUser={currentUser}
              tenant={selectedTenant}
              users={users.filter((user) => user.tenant_id === selectedTenant.id)}
              busy={busy}
              onEdit={() => setDrawer({ kind: "edit-tenant", tenant: selectedTenant })}
              onAddVillage={() => setDrawer({ kind: "add-village", tenant: selectedTenant })}
              onEditUnit={(unit) => setDrawer({ kind: "edit-unit", unit })}
              onSuspend={() => setSuspendTarget({ kind: "tenant", tenant: selectedTenant })}
              onRestore={() =>
                void run(
                  () => updateManagedTenant(selectedTenant.id, { status: "active" }),
                  "租户已恢复使用。",
                )
              }
              onSuspendUnit={(unit) => setSuspendTarget({ kind: "unit", unit })}
              onRestoreUnit={(unit) =>
                void run(
                  () => updateManagedUnit(unit.id, { status: "active" }),
                  `${unit.name}已恢复使用。`,
                )
              }
            />
          ) : selectedUser ? (
            <UserInspector
              currentUser={currentUser}
              user={selectedUser}
              busy={busy}
              onEdit={() => editUser(selectedUser)}
              onReset={() => setDrawer({ kind: "reset-password", user: selectedUser })}
              onSuspend={() => setSuspendTarget({ kind: "user", user: selectedUser })}
              onRestore={() =>
                void run(
                  () => updateManagedUser(selectedUser.user_id, { status: "active" }),
                  "用户已恢复使用。",
                )
              }
            />
          ) : (
            <div className="access-inspector__empty">
              <strong>选择一个管理对象</strong>
              <p>权限边界和可执行操作会在这里显示。</p>
            </div>
          )}
        </aside>
      </div>

      {drawer ? (
        <AccessDrawerPanel
          drawer={drawer}
          tenants={tenants}
          busy={busy}
          createTenantId={createTenantId}
          createRole={createRole}
          createScopeUnits={createScopeUnits}
          createScopeId={createScopeId}
          editTenantId={editTenantId}
          editRole={editRole}
          onClose={() => setDrawer(null)}
          onCreateTenantChange={(tenantId) => {
            const tenant = tenants.find((item) => item.id === tenantId);
            setCreateTenantId(tenantId);
            setCreateRole(tenant?.kind === "platform" ? "platform_admin" : "tenant_admin");
            setCreateScopeId("");
          }}
          onCreateRoleChange={(role) => {
            setCreateRole(role);
            setCreateScopeId("");
          }}
          onCreateScopeChange={setCreateScopeId}
          onAddScope={addCreateScope}
          onEditTenantChange={setEditTenantId}
          onEditRoleChange={setEditRole}
          onSubmitTenant={submitTenant}
          onSubmitUser={submitUser}
          onSubmitDrawer={submitDrawer}
        />
      ) : null}

      {suspendTarget ? (
        <SuspendDialog
          target={suspendTarget}
          busy={busy}
          onCancel={() => setSuspendTarget(null)}
          onConfirm={() => void confirmSuspend()}
        />
      ) : null}
    </section>
  );
}

function TenantInspector({
  currentUser,
  tenant,
  users,
  busy,
  onEdit,
  onAddVillage,
  onEditUnit,
  onSuspend,
  onRestore,
  onSuspendUnit,
  onRestoreUnit,
}: {
  currentUser: CurrentUser;
  tenant: ManagedTenant;
  users: ManagedUser[];
  busy: boolean;
  onEdit: () => void;
  onAddVillage: () => void;
  onEditUnit: (unit: ManagedUnit) => void;
  onSuspend: () => void;
  onRestore: () => void;
  onSuspendUnit: (unit: ManagedUnit) => void;
  onRestoreUnit: (unit: ManagedUnit) => void;
}) {
  const townships = tenant.units.filter((unit) => unit.unit_type === "township");
  const villages = tenant.units.filter((unit) => unit.unit_type === "village");
  return (
    <>
      <header className="access-inspector__header">
        <span>权限边界</span>
        <h2>{tenant.name}</h2>
        <p>{tenant.kind === "platform" ? "控制面租户" : "业务租户"}</p>
        <StatusBadge status={tenant.status} />
      </header>
      <div className="access-inspector__content">
        <section className="access-boundary-card">
          <span>ACTOR → TARGET</span>
          <dl>
            <div><dt>当前身份</dt><dd>{currentUser.display_name} · {roleLabels[currentUser.role]}</dd></div>
            <div><dt>管理目标</dt><dd>{tenant.name}</dd></div>
            <div><dt>数据职责</dt><dd>{tenant.kind === "platform" ? "只管理身份，不承载村级数据" : "承载本租户行政范围内业务数据"}</dd></div>
          </dl>
        </section>
        <section className="access-detail-section">
          <header><h3>行政区划树</h3><span>{villages.length} 个村</span></header>
          {tenant.units.length ? (
            <ul className="access-unit-tree" role="tree" aria-label={`${tenant.name}行政区划`}>
              {townships.map((township) => (
                <li key={township.id} role="treeitem" aria-expanded="true">
                  <UnitRow
                    unit={township}
                    onEdit={onEditUnit}
                    onSuspend={onSuspendUnit}
                    onRestore={onRestoreUnit}
                  />
                  <ul role="group">
                    {villages
                      .filter((village) => village.parent_id === township.id)
                      .map((village) => (
                        <li key={village.id} role="treeitem">
                          <UnitRow
                            unit={village}
                            onEdit={onEditUnit}
                            onSuspend={onSuspendUnit}
                            onRestore={onRestoreUnit}
                          />
                        </li>
                      ))}
                  </ul>
                </li>
              ))}
            </ul>
          ) : (
            <p className="access-detail-empty">当前租户没有行政区划。</p>
          )}
        </section>
        <section className="access-detail-section">
          <header><h3>用户与角色</h3><span>{users.length} 个用户</span></header>
          <dl className="access-role-summary">
            {(["platform_admin", "tenant_admin", "village_operator"] as ManagedRole[]).map((role) => (
              <div key={role}>
                <dt>{roleLabels[role]}</dt>
                <dd>{users.filter((user) => user.role === role).length}</dd>
              </div>
            ))}
          </dl>
        </section>
        <footer className="access-detail-actions">
          <button type="button" onClick={onEdit}>修改租户</button>
          {tenant.kind === "business" && tenant.status === "active" ? (
            <button type="button" onClick={onAddVillage}>新增村</button>
          ) : null}
          {tenant.kind === "business" ? (
            tenant.status === "active" ? (
              <button className="danger" type="button" disabled={busy} onClick={onSuspend}>
                停用租户
              </button>
            ) : (
              <button className="primary" type="button" disabled={busy} onClick={onRestore}>
                恢复使用
              </button>
            )
          ) : null}
        </footer>
      </div>
    </>
  );
}

function UnitRow({
  unit,
  onEdit,
  onSuspend,
  onRestore,
}: {
  unit: ManagedUnit;
  onEdit: (unit: ManagedUnit) => void;
  onSuspend: (unit: ManagedUnit) => void;
  onRestore: (unit: ManagedUnit) => void;
}) {
  return (
    <div className="access-unit-row">
      <span className="access-unit-row__marker" aria-hidden="true" />
      <div>
        <strong>{unit.name}</strong>
        <small>{unit.unit_type === "township" ? "乡镇" : "村"} · {unit.status === "active" ? "启用" : "已停用"}</small>
      </div>
      <button type="button" onClick={() => onEdit(unit)}>修改</button>
      {unit.status === "active" ? (
        <button type="button" onClick={() => onSuspend(unit)}>停用</button>
      ) : (
        <button type="button" onClick={() => onRestore(unit)}>恢复</button>
      )}
    </div>
  );
}

function UserInspector({
  currentUser,
  user,
  busy,
  onEdit,
  onReset,
  onSuspend,
  onRestore,
}: {
  currentUser: CurrentUser;
  user: ManagedUser;
  busy: boolean;
  onEdit: () => void;
  onReset: () => void;
  onSuspend: () => void;
  onRestore: () => void;
}) {
  const isSelf = currentUser.user_id === user.user_id;
  return (
    <>
      <header className="access-inspector__header">
        <span>权限边界</span>
        <h2>{user.display_name}</h2>
        <p>@{user.username}</p>
        <StatusBadge status={user.user_status} />
      </header>
      <div className="access-inspector__content">
        <section className="access-boundary-card">
          <span>ACTOR → TARGET</span>
          <dl>
            <div><dt>当前身份</dt><dd>{currentUser.display_name} · {roleLabels[currentUser.role]}</dd></div>
            <div><dt>管理目标</dt><dd>{user.display_name} · @{user.username}</dd></div>
            <div><dt>目标租户</dt><dd>{user.tenant_name}</dd></div>
          </dl>
        </section>
        <dl className="access-user-definition">
          <div><dt>固定角色</dt><dd>{roleLabels[user.role]}</dd></div>
          <div><dt>行政范围</dt><dd>{user.scope_unit_name || "无行政范围"}</dd></div>
          <div><dt>租户类型</dt><dd>{user.tenant_kind === "platform" ? "控制面租户" : "业务租户"}</dd></div>
          <div><dt>账号状态</dt><dd>{user.user_status === "active" ? "启用" : "已停用"}</dd></div>
        </dl>
        <section className="access-session-note">
          <strong>会话影响</strong>
          <p>修改角色、所属租户、行政范围或密码时，系统会撤销该用户原有登录会话。</p>
        </section>
        <footer className="access-detail-actions">
          <button type="button" onClick={onEdit}>修改身份</button>
          <button type="button" onClick={onReset}>重置密码</button>
          {!isSelf ? (
            user.user_status === "active" ? (
              <button className="danger" type="button" disabled={busy} onClick={onSuspend}>
                停用用户
              </button>
            ) : (
              <button className="primary" type="button" disabled={busy} onClick={onRestore}>
                恢复使用
              </button>
            )
          ) : (
            <span>当前登录身份不能停用自身</span>
          )}
        </footer>
      </div>
    </>
  );
}

function AccessDrawerPanel({
  drawer,
  tenants,
  busy,
  createTenantId,
  createRole,
  createScopeUnits,
  createScopeId,
  editTenantId,
  editRole,
  onClose,
  onCreateTenantChange,
  onCreateRoleChange,
  onCreateScopeChange,
  onAddScope,
  onEditTenantChange,
  onEditRoleChange,
  onSubmitTenant,
  onSubmitUser,
  onSubmitDrawer,
}: {
  drawer: Exclude<AccessDrawer, null>;
  tenants: ManagedTenant[];
  busy: boolean;
  createTenantId: string;
  createRole: ManagedRole;
  createScopeUnits: ManagedUnit[];
  createScopeId: string;
  editTenantId: string;
  editRole: ManagedRole;
  onClose: () => void;
  onCreateTenantChange: (tenantId: string) => void;
  onCreateRoleChange: (role: ManagedRole) => void;
  onCreateScopeChange: (scopeId: string) => void;
  onAddScope: (name: string) => Promise<string>;
  onEditTenantChange: (tenantId: string) => void;
  onEditRoleChange: (role: ManagedRole) => void;
  onSubmitTenant: (event: FormEvent<HTMLFormElement>) => void;
  onSubmitUser: (event: FormEvent<HTMLFormElement>) => void;
  onSubmitDrawer: (event: FormEvent<HTMLFormElement>) => void;
}) {
  const [scopeDraft, setScopeDraft] = useState<string | null>(null);
  const [scopeSaving, setScopeSaving] = useState(false);
  const [scopeError, setScopeError] = useState("");

  async function submitNewScope() {
    const name = (scopeDraft ?? "").trim();
    if (!name) return;
    setScopeSaving(true);
    setScopeError("");
    try {
      const unitId = await onAddScope(name);
      onCreateScopeChange(unitId);
      setScopeDraft(null);
    } catch (cause) {
      setScopeError(cause instanceof Error ? cause.message : "新增行政范围失败");
    } finally {
      setScopeSaving(false);
    }
  }

  const drawerTitle = {
    "create-tenant": "新增业务租户",
    "create-user": "新增用户",
    "edit-tenant": "修改租户",
    "add-village": "新增村",
    "edit-unit": "修改行政区划",
    "edit-user": "修改用户",
    "reset-password": "重置密码",
  }[drawer.kind];
  const editUnits =
    tenants
      .find((tenant) => tenant.id === editTenantId)
      ?.units.filter((unit) => unit.status === "active")
      .filter((unit) =>
        editRole === "tenant_admin"
          ? unit.unit_type === "township"
          : unit.unit_type === "village",
      ) ?? [];
  return (
    <div className="drawer-layer" role="presentation" onMouseDown={onClose}>
      <aside
        className="side-drawer access-drawer"
        aria-label="用户与租户操作"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header>
          <div><span>权限管理</span><h2>{drawerTitle}</h2></div>
          <button type="button" aria-label="关闭" onClick={onClose}>×</button>
        </header>
        {drawer.kind === "create-tenant" ? (
          <form onSubmit={onSubmitTenant}>
            <label>租户名称<input name="name" required placeholder="例如：Y租户" /></label>
            <label>根乡镇名称<input name="township_name" required placeholder="例如：青山镇" /></label>
            <p className="drawer-guidance">创建后可继续增加村级行政范围。</p>
            <DrawerFooter busy={busy} submitLabel="创建租户" onClose={onClose} />
          </form>
        ) : drawer.kind === "create-user" ? (
          <form onSubmit={onSubmitUser}>
            <label>
              所属租户
              <select value={createTenantId} onChange={(event) => onCreateTenantChange(event.target.value)} required>
                {tenants.filter((tenant) => tenant.status === "active").map((tenant) => (
                  <option key={tenant.id} value={tenant.id}>{tenant.name}</option>
                ))}
              </select>
            </label>
            <label>
              角色
              <select value={createRole} onChange={(event) => onCreateRoleChange(event.target.value as ManagedRole)}>
                {tenants.find((tenant) => tenant.id === createTenantId)?.kind === "platform" ? (
                  <option value="platform_admin">平台管理员</option>
                ) : (
                  <>
                    <option value="tenant_admin">租户管理员</option>
                    <option value="village_operator">村级数据员</option>
                  </>
                )}
              </select>
            </label>
            {createRole !== "platform_admin" ? (
              <>
                <label>
                  行政范围
                  <select
                    name="scope_unit_id"
                    required
                    value={createScopeId}
                    onChange={(event) => onCreateScopeChange(event.target.value)}
                  >
                    <option value="" disabled>请选择范围</option>
                    {createScopeUnits.map((unit) => <option key={unit.id} value={unit.id}>{unit.name}</option>)}
                  </select>
                </label>
                {createRole === "village_operator" ? (
                  scopeDraft === null ? (
                    <button
                      type="button"
                      className="access-add-scope"
                      onClick={() => {
                        setScopeDraft("");
                        setScopeError("");
                      }}
                    >
                      + 新增村级行政范围
                    </button>
                  ) : (
                    <div className="access-add-scope-form">
                      <input
                        value={scopeDraft}
                        placeholder="村名称，例如：和平村"
                        autoFocus
                        onChange={(event) => setScopeDraft(event.target.value)}
                      />
                      <button
                        type="button"
                        disabled={scopeSaving || !scopeDraft.trim()}
                        onClick={() => void submitNewScope()}
                      >
                        {scopeSaving ? "新增中…" : "确定"}
                      </button>
                      <button
                        type="button"
                        disabled={scopeSaving}
                        onClick={() => {
                          setScopeDraft(null);
                          setScopeError("");
                        }}
                      >
                        取消
                      </button>
                    </div>
                  )
                ) : null}
                {scopeError ? (
                  <p className="alert access-add-scope-error" role="alert">{scopeError}</p>
                ) : null}
              </>
            ) : null}
            <label>用户名<input name="username" required /></label>
            <label>显示名称<input name="display_name" required /></label>
            <label>初始密码<input name="password" type="password" minLength={4} required /></label>
            <DrawerFooter busy={busy} submitLabel="创建用户" onClose={onClose} />
          </form>
        ) : (
          <form onSubmit={onSubmitDrawer}>
            {drawer.kind === "edit-tenant" ? (
              <label>租户名称<input name="name" required defaultValue={drawer.tenant.name} /></label>
            ) : null}
            {drawer.kind === "add-village" ? (
              <>
                <p className="drawer-guidance">所属租户：{drawer.tenant.name}</p>
                <label>村名称<input name="name" required autoFocus /></label>
              </>
            ) : null}
            {drawer.kind === "edit-unit" ? (
              <label>行政区划名称<input name="name" required defaultValue={drawer.unit.name} /></label>
            ) : null}
            {drawer.kind === "edit-user" ? (
              <>
                <p className="drawer-guidance">保存后将撤销该用户原有登录会话。</p>
                <label>用户名<input name="username" required defaultValue={drawer.user.username} /></label>
                <label>显示名称<input name="display_name" required defaultValue={drawer.user.display_name} /></label>
                <label>
                  所属租户
                  <select value={editTenantId} onChange={(event) => onEditTenantChange(event.target.value)}>
                    {tenants.filter((tenant) => tenant.status === "active").map((tenant) => (
                      <option key={tenant.id} value={tenant.id}>{tenant.name}</option>
                    ))}
                  </select>
                </label>
                <label>
                  角色
                  <select value={editRole} onChange={(event) => onEditRoleChange(event.target.value as ManagedRole)}>
                    <option value="platform_admin">平台管理员</option>
                    <option value="tenant_admin">租户管理员</option>
                    <option value="village_operator">村级数据员</option>
                  </select>
                </label>
                {editRole !== "platform_admin" ? (
                  <label>
                    行政范围
                    <select name="scope_unit_id" required defaultValue={drawer.user.scope_unit_id ?? ""}>
                      <option value="" disabled>请选择范围</option>
                      {editUnits.map((unit) => <option key={unit.id} value={unit.id}>{unit.name}</option>)}
                    </select>
                  </label>
                ) : null}
              </>
            ) : null}
            {drawer.kind === "reset-password" ? (
              <>
                <p className="drawer-guidance">将为 @{drawer.user.username} 设置新密码，并撤销原登录会话。</p>
                <label>新密码<input name="password" type="password" minLength={4} required autoFocus /></label>
              </>
            ) : null}
            <DrawerFooter busy={busy} submitLabel="保存更改" onClose={onClose} />
          </form>
        )}
      </aside>
    </div>
  );
}

function DrawerFooter({
  busy,
  submitLabel,
  onClose,
}: {
  busy: boolean;
  submitLabel: string;
  onClose: () => void;
}) {
  return (
    <footer>
      <button className="button button--ghost" type="button" onClick={onClose}>取消</button>
      <button className="button button--primary" disabled={busy} type="submit">
        {busy ? "正在保存…" : submitLabel}
      </button>
    </footer>
  );
}

function SuspendDialog({
  target,
  busy,
  onCancel,
  onConfirm,
}: {
  target: Exclude<SuspendTarget, null>;
  busy: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const name =
    target.kind === "tenant"
      ? target.tenant.name
      : target.kind === "user"
        ? `${target.user.display_name}（@${target.user.username}）`
        : target.unit.name;
  const kindLabel =
    target.kind === "tenant" ? "租户" : target.kind === "user" ? "用户" : "行政区划";
  return (
    <div className="access-confirm-layer" role="presentation">
      <section
        className="access-confirm"
        role="dialog"
        aria-modal="true"
        aria-labelledby="suspend-title"
      >
        <span>LOGICAL SUSPENSION</span>
        <h2 id="suspend-title">停用{kindLabel}“{name}”？</h2>
        <p>
          这是逻辑停用，不会删除历史记录或审计证据。
          {target.kind === "user" ? "该用户现有登录会话将立即失效。" : ""}
          {target.kind === "tenant" ? "该租户将不能继续开展新的业务操作。" : ""}
        </p>
        <footer>
          <button type="button" onClick={onCancel}>取消</button>
          <button className="danger-button" type="button" disabled={busy} onClick={onConfirm}>
            {busy ? "正在停用…" : `确认停用${kindLabel}`}
          </button>
        </footer>
      </section>
    </div>
  );
}
