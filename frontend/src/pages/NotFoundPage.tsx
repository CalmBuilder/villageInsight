import { Link } from "react-router-dom";

export function NotFoundPage() {
  return (
    <section className="not-found">
      <span className="coordinate">404</span>
      <div>
        <h2>没有找到这个页面</h2>
        <p>当前地址不属于村知数工作台。</p>
        <Link className="primary-button" to="/batches">
          返回处理台账
        </Link>
      </div>
    </section>
  );
}
