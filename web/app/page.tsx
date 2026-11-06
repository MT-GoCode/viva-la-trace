import { Suspense } from "react";
import { feed, stats } from "@/lib/server";
import Live from "./live";

async function LiveData() {
  const [s, f] = await Promise.all([stats(), feed(null)]);
  return <Live stats={s} feed={f} />;
}

export default function Home() {
  return (
    <main>
      <h1 className="title" aria-label="Viva la trace">
        <span className="streaks" aria-hidden />
        <span className="ghost" aria-hidden>VIVA LA<br />TRACE</span>
        <span className="word">VIVA LA<br />TRACE</span>
      </h1>
      <Suspense>
        <LiveData />
      </Suspense>
    </main>
  );
}
