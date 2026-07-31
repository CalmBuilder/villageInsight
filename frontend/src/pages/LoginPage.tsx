import { useState } from "react";
import { BrandMark } from "../components/BrandMark";
import { login, type CurrentUser } from "../lib/api";

export function LoginPage({
  onAuthenticated,
}: {
  onAuthenticated: (user: CurrentUser) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setError("");
    try {
      const user = await login(
        String(form.get("username") ?? "").trim(),
        String(form.get("password") ?? ""),
      );
      onAuthenticated(user);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "登录失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="login-stage">
      <section className="login-copy">
        <div className="login-brand">
          <BrandMark className="login-brand__mark" />
          <span>
            <strong>村知数</strong>
            <small>VILLAGEINSIGHT</small>
          </span>
        </div>
        <h1>
          村情数据，
          <br />
          各归<em>其位</em>
        </h1>
        <p>
          村级数据员负责本村文件入库；租户管理员可跨村入库并获得租户范围确定性答案。
        </p>
        <ul className="login-points">
          <li>
            <strong>物理证据留痕</strong>
            <span>解析结果可回溯到原表单元格坐标</span>
          </li>
          <li>
            <strong>人工审核入库</strong>
            <span>模板版本化，每一次写入确定可追溯</span>
          </li>
          <li>
            <strong>可信问数</strong>
            <span>答案回到原表坐标，而非模型猜测</span>
          </li>
        </ul>
        <div className="login-boundary">
          <span>可信边界</span>
          <strong>身份、行政范围与答案证据由平台统一校验</strong>
        </div>
      </section>
      <form className="login-form" onSubmit={submit}>
        <header>
          <span>
            <small>账号登录</small>
            <strong>村知数</strong>
          </span>
          <BrandMark className="login-form__mark" />
        </header>
        <label htmlFor="username">用户名</label>
        <input id="username" name="username" required autoComplete="username" />
        <label htmlFor="password">密码</label>
        <input
          id="password"
          name="password"
          required
          type="password"
          autoComplete="current-password"
        />
        {error ? <p className="alert" role="alert">{error}</p> : null}
        <button className="primary-button" disabled={busy} type="submit">
          {busy ? "正在验证…" : "进入工作台"}
        </button>
      </form>
    </main>
  );
}
