# Three stages, one per way this app is served.
#
# `dev` is what `docker compose up` runs and what every gate in scripts/verify.sh
# drives: Vite's dev server with HMR, bind-mounted over the source. It installs
# with `npm install` rather than `npm ci` on purpose — the dev stack is meant to
# survive a package.json edit without a rebuild.
#
# `build` and `prod` are the deployment, layered on by docker-compose.prod.yml.
# The difference that matters between them and `dev` is not image size or startup
# time: **`npm run build` is the only thing that produces `dist/sw.js`**, and with
# no service worker the app cannot display a desktop notification at all — the
# panel says as much on /admin rather than offering a switch that changes nothing
# (ADR-0037, and `src/lib/notify.ts`'s `canShow`). `vite-plugin-pwa` emits the
# worker only for a production build, so the dev stack has no way to reach that
# state, which is what this stage is for.

FROM node:22-bookworm-slim AS dev

WORKDIR /app

# Install deps first for layer caching.
COPY package.json ./
RUN npm install

COPY . .

EXPOSE 5173

CMD ["npm", "run", "dev"]

FROM node:22-bookworm-slim AS build

WORKDIR /app

# From the lock, unlike `dev`: a deployed image is the one place reproducibility
# is worth having to rebuild after every dependency bump. `package-lock.json` is
# committed, so `npm ci` is available and is what refuses to quietly resolve a
# different tree than the one that was tested.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# `npm run build` is `tsc --noEmit && vite build`, so a type error fails the
# image rather than shipping a bundle whose types disagree with it.
RUN npm run build

FROM nginx:1.27-alpine AS prod

# The server config, not the main nginx.conf: the image's own file already sets
# `user nginx` and the entrypoint handles the rest, and replacing it wholesale
# would mean owning all of that too. Everything this app needs is a server block.
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 8080

# No CMD or ENTRYPOINT: the nginx image's own (`nginx -g 'daemon off;'`) is what
# we want, and restating it here would be a second copy to keep in sync.
