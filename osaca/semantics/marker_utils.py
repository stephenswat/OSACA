#!/usr/bin/env python3
from collections import OrderedDict

from osaca.parser import ParserAArch64v81, ParserX86ATT


def reduce_to_section(kernel, isa):
    isa = isa.lower()
    if isa == 'x86':
        start, end = find_marked_kernel_x86ATT(kernel)
    elif isa == 'aarch64':
        start, end = find_marked_kernel_AArch64(kernel)
    else:
        raise ValueError('ISA not supported.')
    if start == -1:
        raise LookupError('Could not find START MARKER. Make sure it is inserted!')
    if end == -1:
        raise LookupError('Could not find END MARKER. Make sure it is inserted!')
    return kernel[start:end]


def find_marked_kernel_AArch64(lines):
    nop_bytes = ['213', '3', '32', '31']
    return find_marked_section(
        lines, ParserAArch64v81(), ['mov'], 'x1', [111, 222], nop_bytes, reverse=True
    )


def find_marked_kernel_x86ATT(lines):
    nop_bytes = ['100', '103', '144']
    return find_marked_section(lines, ParserX86ATT(), ['mov', 'movl'], 'ebx', [111, 222], nop_bytes)


def find_marked_section(lines, parser, mov_instr, mov_reg, mov_vals, nop_bytes, reverse=False):
    index_start = -1
    index_end = -1
    for i, line in enumerate(lines):
        try:
            if line.instruction in mov_instr and lines[i + 1].directive is not None:
                source = line.operands[0 if not reverse else 1]
                destination = line.operands[1 if not reverse else 0]
                # instruction pair matches, check for operands
                if (
                    'immediate' in source
                    and parser.normalize_imd(source.immediate) == mov_vals[0]
                    and 'register' in destination
                    and parser.get_full_reg_name(destination.register) == mov_reg
                ):
                    # operands of first instruction match start, check for second one
                    match, line_count = match_bytes(lines, i + 1, nop_bytes)
                    if match:
                        # return first line after the marker
                        index_start = i + 1 + line_count
                elif (
                    'immediate' in source
                    and parser.normalize_imd(source.immediate) == mov_vals[1]
                    and 'register' in destination
                    and parser.get_full_reg_name(destination.register) == mov_reg
                ):
                    # operand of first instruction match end, check for second one
                    match, line_count = match_bytes(lines, i + 1, nop_bytes)
                    if match:
                        # return line of the marker
                        index_end = i
        except TypeError:
            print(i, line)
        if index_start != -1 and index_end != -1:
            break
    return index_start, index_end


def match_bytes(lines, index, byte_list):
    # either all bytes are in one line or in separate ones
    extracted_bytes = []
    line_count = 0
    while (
        index < len(lines)
        and lines[index].directive is not None
        and lines[index].directive.name == 'byte'
    ):
        line_count += 1
        extracted_bytes += lines[index].directive.parameters
        index += 1
    if extracted_bytes[0:len(byte_list)] == byte_list:
        return True, line_count
    return False, -1


def find_jump_labels(lines):
    """
    Find and return all labels which are followed by instructions

    :return: OrderedDict of mapping from label name to associated line
    """
    # 1. Identify labels and instructions until next label
    labels = OrderedDict()
    current_label = None
    for i, line in enumerate(lines):
        if line['label'] is not None:
            # When a new label is found, add to blocks dict
            labels[line['label']] = (i, )
            # End previous block at previous line
            if current_label is not None:
                labels[current_label] = (labels[current_label][0], i)
            # Update current block name
            current_label = line['label']
        elif current_label is None:
            # If no block has been started, skip end detection
            continue
    # Set to last line if no end was for last label found
    if current_label is not None and len(labels[current_label]) == 1:
        labels[current_label] = (labels[current_label][0], i+1)

    # 2. Identify and remove labels which contain only dot-instructions (e.g., .text)
    for label in list(labels):
        if all([l['instruction'].startswith('.')
                for l in lines[labels[label][0]:labels[label][1]] if l['instruction'] is not None]):
            del labels[label]

    return OrderedDict([(l, lines[v[0]]) for l, v in labels.items()])


def find_basic_blocks(lines):
    """
    Find and return basic blocks (asm sections which can only be executed as complete block).

    Blocks always start at a label and end at the next jump/break possibility.

    :return: OrderedDict with labels as keys and list of lines as value
    """
    valid_jump_labels = find_jump_labels(lines)

    # Identify blocks, as they are started with a valid jump label and terminated by a label or
    # an instruction referencing a valid jump label
    blocks = OrderedDict()
    for label, label_line in valid_jump_labels.items():
        blocks[label] = []
        for i in range(lines.index(label_line)):
            terminate = False
            line = lines[i]
            blocks[label].append(line)
            # Find end of block by searching for references to valid jump labels
            if line['instruction'] and line['operands']:
                for operand in [o for o in line['operands'] if 'identifier' in o]:
                    if operand['identifier']['name'] in valid_jump_labels:
                        terminate = True
            elif line['label'] is not None:
                terminate = True
            if terminate:
                break

    return blocks


def find_basic_loop_bodies(lines):
    """
    Find and return basic loop bodies (asm section which loop back on itself with no other egress).

    :return: OrderedDict with labels as keys and list of lines as value
    """
    valid_jump_labels = find_jump_labels(lines)

    # Identify blocks, as they are started with a valid jump label and terminated by
    # an instruction referencing a valid jump label
    loop_bodies = OrderedDict()
    for label, label_line in valid_jump_labels.items():
        current_block = []
        for i in range(lines.index(label_line), len(lines)):
            terminate = False
            line = lines[i]
            current_block.append(line)
            # Find end of block by searching for references to valid jump labels
            if line['instruction'] and line['operands']:
                for operand in [o for o in line['operands'] if 'identifier' in o]:
                    if operand['identifier']['name'] in valid_jump_labels:
                        if operand['identifier']['name'] == label:
                            loop_bodies[label] = current_block
                        terminate = True
                        break
            if terminate:
                break

    return loop_bodies
