/// <reference types="vite/client" />

/** Import de fichier brut (`?raw`), utilisé par le test de contrat pour relire `client.ts`. */
declare module '*?raw' {
  const content: string
  export default content
}
