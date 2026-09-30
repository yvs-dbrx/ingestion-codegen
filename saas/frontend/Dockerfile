# Dev image for the SaaS frontend. Source is bind-mounted over /app by docker-compose.yml,
# so edits hot-reload through Vite; node_modules is installed into the image (not mounted),
# matching the pattern the backend Dockerfile uses for pip installs.
FROM node:22-slim

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host"]
