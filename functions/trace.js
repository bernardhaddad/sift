// Cloudflare Pages Function at /trace: the same relay as relay/worker.js
import relay from "../relay/worker.js";

export const onRequest = (context) => relay.fetch(context.request, context.env);
