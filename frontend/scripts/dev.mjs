import { createServer } from 'vite';
import { createServer as createSocket } from 'node:net';

// Vite accepts numeric ports; translate the repository's --port auto convention.
const args = process.argv.slice(2);
const portIndex = args.indexOf('--port');
const portArg = portIndex < 0 ? 'auto' : args[portIndex + 1];
if (portArg !== 'auto' && !/^\d+$/.test(portArg ?? '')) {
  throw new Error('Use --port auto or a numeric port.');
}
// Vite treats port 0 as its default; ask the OS for an available numeric port.
async function availablePort() {
  const socket = createSocket();
  await new Promise((resolve, reject) => {
    socket.once('error', reject);
    socket.listen(0, '127.0.0.1', resolve);
  });
  const { port } = socket.address();
  await new Promise((resolve, reject) => socket.close(error => error ? reject(error) : resolve()));
  return port;
}
const server = await createServer({ server: { port: portArg === 'auto' ? await availablePort() : Number(portArg), strictPort: true } });
await server.listen();
server.printUrls();
