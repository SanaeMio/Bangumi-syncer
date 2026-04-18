import { defineConfig } from 'vitepress'
import { withSidebar } from 'vitepress-sidebar'

const SITE = 'https://sanaemio.github.io'

export default defineConfig(
  withSidebar(
    {
      title: 'Bangumi-syncer',
      description:
        '通过 Webhook 与 Bangumi API，在 Plex / Emby / Jellyfin 等看完后自动同步打格子。',
      base: '/Bangumi-syncer/',
      lang: 'zh-Hans',
      cleanUrls: true,
      lastUpdated: true,
      sitemap: {
        hostname: `${SITE}/Bangumi-syncer/`,
      },
      themeConfig: {
        logo: '/images/branding/logo_simple.png',
        appearance: true,
        nav: [
          { text: '首页', link: '/' },
          { text: '文档', link: '/intro' },
        ],
        outline: { label: '本页目录' },
        docFooter: { prev: '上一页', next: '下一页' },
        lastUpdated: {
          text: '更新于',
          formatOptions: { dateStyle: 'short', timeStyle: 'short' },
        },
        socialLinks: [
          {
            icon: 'github',
            link: 'https://github.com/SanaeMio/Bangumi-syncer',
          },
        ],
        search: {
          provider: 'local',
          options: {
            translations: {
              button: { buttonText: '搜索文档', buttonAriaLabel: '搜索文档' },
              modal: {
                displayDetails: '显示详细列表',
                resetButtonTitle: '清除查询条件',
                noResultsText: '无法找到相关结果',
                footer: {
                  selectText: '选择',
                  navigateText: '切换',
                  closeText: '关闭',
                },
              },
            },
          },
        },
      },
    },
    {
      documentRootPath: '/docs',
      useTitleFromFrontmatter: true,
      sortMenusByFrontmatterOrder: true,
      frontmatterOrderDefaultValue: 999,
      includeRootIndexFile: false,
      includeFolderIndexFile: false,
      useFolderTitleFromIndexFile: true,
      useFolderLinkFromIndexFile: true,
      collapsed: false,
      capitalizeFirst: false,
      hyphenToSpace: true,
      manualSortFileNameByPriority: [
        'intro.md',
        'features.md',
        'quick-start',
        'configuration.md',
        'mapping.md',
        'usage',
        'roadmap.md',
        'community.md',
      ],
      excludeByGlobPattern: ['**/public/**', '**/.vitepress/**'],
    },
  ),
)
