# syntax=docker/dockerfile:1
# Image de développement : Vite en mode dev avec HMR.
# L'image de production (build statique servi par un reverse proxy) arrive en P12.
FROM node:25-slim

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

EXPOSE 5173

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
