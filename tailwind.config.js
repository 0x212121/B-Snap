/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './templates/**/*.html',
    './app/**/*.py',
    './static/**/*.js',
    './node_modules/preline/dist/*.js'
  ],
  darkMode: 'class', // 👈 enable toggle with class="dark"
  theme: {
    extend: {
      colors: {
        primary: '#0ea5e9', // biru terang (sky-500)
        danger: '#ef4444', // merah
        success: '#22c55e', // hijau
        warning: '#facc15', // kuning
        // bebas kamu tambah brand warna di sini
      },
    },
  },
  plugins: [
    require('@tailwindcss/forms'),       // styling form
    require('@tailwindcss/typography'),  // prose / konten
    require('@tailwindcss/aspect-ratio') // video / gambar
  ],
}
