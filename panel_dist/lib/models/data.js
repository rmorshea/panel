export function transform_cds_to_records(cds, addId = false) {
    const data = [];
    const columns = cds.columns();
    const cdsLength = cds.get_length();
    if (columns.length === 0 || cdsLength === null) {
        return [];
    }
    for (let i = 0; i < cdsLength; i++) {
        const item = {};
        for (const column of columns) {
            let array = cds.get_array(column);
            const shape = array[0].shape == null ? null : array[0].shape;
            if ((shape != null) && (shape.length > 1) && (typeof shape[0] == "number"))
                item[column] = array.slice(i * shape[1], i * shape[1] + shape[1]);
            else
                item[column] = array[i];
        }
        if (addId)
            item['_index'] = i;
        data.push(item);
    }
    return data;
}
//# sourceMappingURL=data.js.map