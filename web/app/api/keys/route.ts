import { newKey, route } from "@/lib/server";

export const POST = route(async () => ({ key: await newKey() }));
