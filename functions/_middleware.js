// every path of the Pages project goes through the relay (which redirects stray visits to the radar);
// the context goes along for its waitUntil, so a visit count is written after the answer
import relay from "../relay/worker.js";

export const onRequest = (context) => relay.fetch(context.request, context.env, context);
