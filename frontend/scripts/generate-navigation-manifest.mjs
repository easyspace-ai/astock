// 生成后端可读的源码扩展页导航清单。
// 目的：新增 frontend/src/custom/*/extension.tsx 后，构建产物自带页面清单，
// 后端权限目录/导航树无需人工同步静态清单即可发现新页面。
import fs from 'node:fs'
import path from 'node:path'
import ts from 'typescript'

const root = path.resolve(import.meta.dirname, '..')
const customDir = path.join(root, 'src/custom')
const outFile = path.join(root, 'dist/navigation-manifest.json')

function propName(node) {
  if (ts.isIdentifier(node.name)) return node.name.text
  if (ts.isStringLiteral(node.name)) return node.name.text
  return null
}

function stringValue(node) {
  return ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node) ? node.text : null
}

function numberValue(node) {
  return ts.isNumericLiteral(node) ? Number(node.text) : null
}

function objectProps(node) {
  const values = new Map()
  for (const prop of node.properties) {
    if (!ts.isPropertyAssignment(prop)) continue
    const name = propName(prop)
    if (name) values.set(name, prop.initializer)
  }
  return values
}

const pages = []
for (const entry of fs.readdirSync(customDir, { withFileTypes: true })) {
  if (!entry.isDirectory()) continue
  const file = path.join(customDir, entry.name, 'extension.tsx')
  if (!fs.existsSync(file)) continue
  const source = ts.createSourceFile(file, fs.readFileSync(file, 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
  const routePathById = new Map()
  const navigation = []
  const visit = node => {
    if (ts.isPropertyAssignment(node)) {
      const name = propName(node)
      if (name === 'routes' && ts.isArrayLiteralExpression(node.initializer)) {
        for (const item of node.initializer.elements) {
          if (!ts.isObjectLiteralExpression(item)) continue
          const props = objectProps(item)
          const id = props.get('id') && stringValue(props.get('id'))
          const routePath = props.get('path') && stringValue(props.get('path'))
          if (id && routePath) routePathById.set(id, routePath)
        }
      }
      if (name === 'navigation' && ts.isArrayLiteralExpression(node.initializer)) {
        for (const item of node.initializer.elements) {
          if (!ts.isObjectLiteralExpression(item)) continue
          const props = objectProps(item)
          const routeId = props.get('routeId') && stringValue(props.get('routeId'))
          const label = props.get('label') && stringValue(props.get('label'))
          const order = props.get('order') && numberValue(props.get('order'))
          if (routeId && label) navigation.push({ routeId, label, order: order ?? 100 })
        }
      }
    }
    ts.forEachChild(node, visit)
  }
  visit(source)
  for (const item of navigation) {
    const routePath = routePathById.get(item.routeId)
    if (!routePath) continue
    pages.push({
      extensionId: entry.name,
      path: routePath,
      label: item.label,
      order: item.order,
    })
  }
}

pages.sort((a, b) => a.order - b.order || a.path.localeCompare(b.path))
fs.mkdirSync(path.dirname(outFile), { recursive: true })
fs.writeFileSync(outFile, `${JSON.stringify({ version: 1, pages }, null, 2)}\n`, 'utf8')
console.log(`navigation manifest: ${pages.length} pages -> ${outFile}`)
