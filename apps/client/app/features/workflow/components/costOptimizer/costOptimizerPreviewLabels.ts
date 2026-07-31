export type CostOptimizerPreviewFieldLabels = Readonly<Record<string, string>>;

const isUnknownRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

/** JSON Schema title이 있을 때만 화면에 표시할 필드명을 제공한다. */
export const fieldLabelsFromOutputFormat = (
  outputFormat: unknown,
): CostOptimizerPreviewFieldLabels => {
  if (!isUnknownRecord(outputFormat) || !isUnknownRecord(outputFormat.schema)) {
    return {};
  }

  const properties = outputFormat.schema.properties;
  if (!isUnknownRecord(properties)) return {};

  return Object.fromEntries(
    Object.entries(properties).flatMap(([key, schema]) => {
      const title = isUnknownRecord(schema) ? schema.title : undefined;
      return typeof title === 'string' && title.trim().length > 0
        ? [[key, title.trim()]]
        : [];
    }),
  );
};
