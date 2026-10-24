// every path of the Pages project goes through the relay (which redirects stray visits to the radar)
import relay from "../relay/worker.js";

export const onRequest = (context) => relay.fetch(context.request, context.env);
