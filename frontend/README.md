# Know Your Charity - Frontend

Modern Next.js frontend for the AML SaaS platform.

## Features

- 🎨 **Responsive Design** - Mobile-first Tailwind CSS
- ⚡ **Next.js 15** - React 18 with App Router
- 🔐 **Type-Safe** - Full TypeScript support
- 🎯 **Professional UI** - Clean, modern interface
- 📊 **Dashboard** - Report generation and management
- 💳 **Pricing Page** - Simple pay-as-you-go & subscription

## Quick Start

### Install Dependencies
```bash
npm install
```

### Development
```bash
npm run dev
```

Open http://localhost:3000

### Build
```bash
npm run build
npm start
```

## Project Structure

```
src/
├── app/
│   ├── layout.tsx         # Root layout
│   ├── page.tsx           # Homepage
│   ├── globals.css        # Global styles
│   └── dashboard/
│       └── page.tsx       # Dashboard page
└── components/            # Reusable components (future)
```

## Technology Stack

- **Framework**: Next.js 15
- **Language**: TypeScript
- **Styling**: Tailwind CSS
- **Icons**: Lucide React
- **Package Manager**: npm

## Pages

- **/** - Landing page with features & pricing
- **/dashboard** - Report search & listing
- **/login** - (Coming soon)
- **/signup** - (Coming soon)

## API Integration

Backend API: http://localhost:8000

Set in `.env.local`:
```
NEXT_PUBLIC_API_URL=http://localhost:8000
```

## Deployment

Ready for deployment on:
- Vercel (recommended)
- Netlify
- AWS
- Google Cloud

## License

Proprietary
