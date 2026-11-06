import { feed, route } from "@/lib/server";

export const GET = route(async (req) => feed(new URL(req.url).searchParams.get("tag")), 2);
