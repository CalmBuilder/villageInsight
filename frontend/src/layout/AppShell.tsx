import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { BrandMark } from "../components/BrandMark";
import { changePassword, getReviewQueue, type CurrentUser } from "../lib/api";

const userNavigation = [
  { path: "/batches", label: "文件入库", index: "01" },
  { path: "/questions", label: "可信问数", index: "02" },
] as const;

const adminNavigation = [
  { path: "/admin/access", label: "用户与租户", index: "A0" },
  { path: "/admin/reviews", label: "数据治理", index: "A1" },
  { path: "/admin/records", label: "入库记录", index: "A2" },
  { path: "/admin/catalog", label: "字段与模板", index: "A3" },
  { path: "/admin/settings", label: "模型连接", index: "A4" },
] as const;

const roleLabels: Record<CurrentUser["role"], string> = {
  platform_admin: "平台管理员",
  tenant_admin: "租户管理员",
  village_operator: "村级数据员",
};

const pageHeadings: Record<string, string> = {
  "/": "让每一份村情台账，都有据可查",
  "/batches": "让每一份村情台账，都有据可查",
  "/questions": "答案回到原表坐标，而非模型猜测",
  "/admin": "先完成入库，再集中治理不确定项",
  "/admin/access": "平台身份与业务租户严格分离",
  "/admin/reviews": "先完成入库，再集中治理不确定项",
  "/admin/records": "看清每一条正式入库记录",
  "/admin/catalog": "一次确认，跨村、跨批次复用",
  "/admin/settings": "模型可以更换，可信边界保持不变",
};

export function AppShell({
  space,
  currentUser,
  onLogout,
}: {
  space: "user" | "admin";
  currentUser: CurrentUser;
  onLogout: () => void;
}) {
  const { pathname } = useLocation();
  const [reviewCount, setReviewCount] = useState(0);
  const [passwordOpen, setPasswordOpen] = useState(false);
  const [passwordBusy, setPasswordBusy] = useState(false);
  const [passwordError, setPasswordError] = useState("");
  const [passwordMessage, setPasswordMessage] = useState("");
  const heading = pageHeadings[pathname] ?? "页面未找到";
  const navigation = space === "admin"
    ? adminNavigation
    : userNavigation.filter(
        (item) =>
          item.path !== "/questions"
          || currentUser.permissions.some((permission) =>
            permission.startsWith("questions.ask."),
          ),
      );
  const isFileWorkspace = pathname === "/" || pathname === "/batches";
  const isQuestionWorkspace = pathname === "/questions";
  const isPlatformReadOnly =
    space === "user" && currentUser.role === "platform_admin";
  const mainClassName = isFileWorkspace
    ? "main--workspace"
    : isQuestionWorkspace
      ? "main--question-workspace"
      : undefined;
  const shellClassName = isQuestionWorkspace
    ? "shell shell--question-workspace"
    : "shell";

  useEffect(() => {
    if (currentUser.role !== "platform_admin") return;
    const controller = new AbortController();
    getReviewQueue({}, controller.signal)
      .then((reviews) => setReviewCount(reviews.total))
      .catch(() => undefined);
    return () => controller.abort();
  }, [pathname, currentUser.role]);

  async function submitPassword(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    const currentPassword = String(values.get("current_password") ?? "");
    const newPassword = String(values.get("new_password") ?? "");
    const confirmation = String(values.get("confirmation") ?? "");
    if (newPassword !== confirmation) {
      setPasswordError("两次输入的新密码不一致");
      return;
    }
    setPasswordBusy(true);
    setPasswordError("");
    setPasswordMessage("");
    try {
      await changePassword(currentPassword, newPassword);
      form.reset();
      setPasswordMessage("密码已修改，其他登录会话已退出。");
    } catch (cause) {
      setPasswordError(cause instanceof Error ? cause.message : "密码修改失败");
    } finally {
      setPasswordBusy(false);
    }
  }

  return (
    <div className={shellClassName} data-space={space}>
      <header className="masthead">
        <Link
          className="brand"
          to={space === "admin" ? "/admin/access" : "/batches"}
        >
          <span className="brand__mark" aria-hidden="true">
            <BrandMark />
          </span>
          <span>
            <strong>村知数</strong>
            <small>VILLAGEINSIGHT</small>
          </span>
          {isQuestionWorkspace && (
            <span className="brand__tagline">答案回到原表坐标，而非模型猜测</span>
          )}
        </Link>
        <nav aria-label="主导航">
          {navigation.map((item) => (
            <NavLink
              className={({ isActive }) => (isActive ? "active" : undefined)}
              key={item.path}
              to={item.path}
            >
              <span>{item.index}</span>
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="shell-actions">
          <div className="shell-identity">
            <span>当前身份</span>
            <strong>{currentUser.display_name}</strong>
            <small>{roleLabels[currentUser.role]} · {currentUser.tenant_name}</small>
          </div>
          {currentUser.role === "platform_admin" && space === "admin" ? (
            <Link className="space-switch space-switch--primary" to="/batches">
              进入用户端 · 只读
            </Link>
          ) : null}
          {currentUser.role === "platform_admin" && space === "user" ? (
            <Link className="space-switch space-switch--primary" to="/admin/reviews">
              {`返回管理端${reviewCount ? ` · 待治理 ${reviewCount}` : ""}`}
            </Link>
          ) : null}
          <button
            className="space-switch"
            onClick={() => {
              setPasswordOpen(true);
              setPasswordError("");
              setPasswordMessage("");
            }}
            type="button"
          >
            修改密码
          </button>
          <button className="space-switch" onClick={onLogout} type="button">退出</button>
        </div>
      </header>
      <main className={mainClassName}>
        {isPlatformReadOnly && !isFileWorkspace ? (
          <div className="readonly-space-banner" role="status">
            <strong>用户端只读视图</strong>
            <span>当前平台身份可查看全业务租户文件，不可上传、重新入库或发起问数。</span>
          </div>
        ) : null}
        {!isFileWorkspace && !isQuestionWorkspace && <header className="page-intro">
          <div>
            <span className="eyebrow">STRUCTURED VILLAGE RECORDS</span>
            <h1>{heading}</h1>
          </div>
          <p>
            {space === "admin"
              ? "集中处理部分入库后的低置信与冲突项，并治理模版、字段和模型配置。"
              : "批量上传后由后台自动处理；不确定项先部分入库，再由管理员集中治理。"}
          </p>
        </header>}
        <Outlet />
      </main>
      {!isQuestionWorkspace ? (
        <footer>
          <span>VI / 0.1</span>
          <p>单一事实源 · 模板版本化 · 证据坐标可追溯</p>
        </footer>
      ) : null}
      {passwordOpen ? (
        <div
          className="drawer-layer"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setPasswordOpen(false);
          }}
        >
          <aside className="side-drawer password-drawer" aria-label="修改密码">
            <header>
              <div><span>账号安全</span><h2>修改密码</h2></div>
              <button type="button" aria-label="关闭修改密码" onClick={() => setPasswordOpen(false)}>×</button>
            </header>
            <form onSubmit={submitPassword}>
              <label>当前密码<input name="current_password" type="password" autoComplete="current-password" required /></label>
              <label>新密码<input name="new_password" type="password" autoComplete="new-password" minLength={8} required /><small>至少 8 个字符。</small></label>
              <label>再次输入新密码<input name="confirmation" type="password" autoComplete="new-password" minLength={8} required /></label>
              {passwordError ? <p className="alert" role="alert">{passwordError}</p> : null}
              {passwordMessage ? <p className="success-note" role="status">{passwordMessage}</p> : null}
              <footer>
                <button className="button button--ghost" type="button" onClick={() => setPasswordOpen(false)}>取消</button>
                <button className="button button--primary" disabled={passwordBusy} type="submit">{passwordBusy ? "正在保存…" : "保存新密码"}</button>
              </footer>
            </form>
          </aside>
        </div>
      ) : null}
    </div>
  );
}
